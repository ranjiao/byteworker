"""Job calendar, outstanding report periods and report candidate eligibility."""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import dreaming_reports
from dreaming_state import DreamingError, parse_time, secure_path, utc_iso


def schedule_time(value: str) -> time:
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except ValueError as exc:
        raise DreamingError(
            "DREAMING_CONFIG_INVALID",
            f"时间必须是 HH:MM: {value}",
        ) from exc
    return parsed.time()


def _last_success(job: Mapping[str, Any]) -> Mapping[str, Any] | None:
    value = job.get("last_success")
    return value if isinstance(value, Mapping) and value.get("status") == "success" else None


def _latest_weekday_target(local_now: datetime, clock: time) -> datetime:
    candidate = local_now.replace(
        hour=clock.hour,
        minute=clock.minute,
        second=0,
        microsecond=0,
    )
    if candidate > local_now:
        candidate -= timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def _latest_weekly_target(
    local_now: datetime,
    *,
    weekday: int,
    clock: time,
) -> datetime:
    days = (local_now.weekday() - weekday) % 7
    candidate = (local_now - timedelta(days=days)).replace(
        hour=clock.hour,
        minute=clock.minute,
        second=0,
        microsecond=0,
    )
    if candidate > local_now:
        candidate -= timedelta(days=7)
    return candidate


def _latest_daily_target(local_now: datetime, clock: time) -> datetime:
    candidate = local_now.replace(
        hour=clock.hour,
        minute=clock.minute,
        second=0,
        microsecond=0,
    )
    return candidate if candidate <= local_now else candidate - timedelta(days=1)


def _every_n_days_target(
    local_now: datetime,
    *,
    days: int,
    clock: time,
    anchor_date: str,
) -> datetime:
    if days <= 0:
        raise DreamingError(
            "DREAMING_STATE_INVALID",
            "every_n_days days 必须大于 0。",
        )
    try:
        anchor = datetime.strptime(anchor_date, "%Y-%m-%d").date()
    except ValueError as exc:
        raise DreamingError(
            "DREAMING_STATE_INVALID",
            "every_n_days anchor_date 无效。",
        ) from exc
    elapsed = (local_now.date() - anchor).days
    step = elapsed // days
    target_date = anchor + timedelta(days=step * days)
    target = local_now.replace(
        year=target_date.year,
        month=target_date.month,
        day=target_date.day,
        hour=clock.hour,
        minute=clock.minute,
        second=0,
        microsecond=0,
    )
    if target > local_now:
        target -= timedelta(days=days)
    return target


def _report_period(name: str, target: datetime) -> str:
    if name == "weekly":
        year, week, _ = (target.date() - timedelta(days=7)).isocalendar()
        return f"{year}-W{week:02d}"
    return target.date().isoformat()


def _restore_daily_report_debt(
    kb: Path, pending: dict[str, Any], *, settled_period: str | None,
    last_attempt: Mapping[str, Any],
    enabled_at: datetime, local_now: datetime,
) -> None:
    """Recover periods that older deadline publishers removed from the formal queue."""
    for path in secure_path(kb, "reports").glob("daily-*/deadline/publication.json"):
        period = path.parent.parent.name.removeprefix("daily-")
        if period == settled_period or period in pending:
            continue
        artifacts = path.parent.parent / "artifacts"
        interrupted = last_attempt.get("period") == period and last_attempt.get("status") in {
            "running", "failed", "partial",
        }
        if (artifacts / "manifest.json").is_file() and not interrupted:
            # Older completed reports can predate the current last_success.
            continue
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
            cutoff = parse_time(receipt.get("cutoff"))
        except (AttributeError, OSError, ValueError):
            continue
        if (
            receipt.get("schema_version") == "byteworker-deadline-daily/v1"
            and receipt.get("status") in {"staged", "published"}
            and receipt.get("period") == period
            and cutoff is not None
            and enabled_at <= cutoff <= local_now
            and cutoff.astimezone(local_now.tzinfo).date().isoformat() == period
        ):
            archive = kb / "reports" / "daily" / f"{period}.md"
            if archive.is_file():
                digest = receipt.get("sha256")
                if not isinstance(digest, str) or len(digest) != 64 or any(
                    char not in "0123456789abcdef" for char in digest
                ):
                    continue
                baseline = path.parent / f"{digest}.md"
                if interrupted and (artifacts / "report.md").is_file():
                    baseline = artifacts / "report.md"
                try:
                    heading = "\n## 手动补充 / 备注\n"
                    archive_body = archive.read_text(encoding="utf-8").split(heading, 1)[0]
                    baseline_body = baseline.read_text(encoding="utf-8").split(heading, 1)[0]
                    if archive_body != baseline_body:
                        continue
                except (OSError, ValueError):
                    continue
            pending[period] = utc_iso(cutoff)


def queue_report_periods(
    kb: Path, name: str, job: dict[str, Any], local_now: datetime, enabled_at: datetime,
) -> None:
    """Discover outstanding formal reports without consuming older debt."""
    schedule = job["schedule"]
    clock = schedule_time(schedule["time"])
    if name == "weekly":
        latest = _latest_weekly_target(local_now, weekday=schedule["weekday"], clock=clock)
        step = timedelta(days=7)
    else:
        latest = _latest_weekday_target(local_now, clock)
        step = timedelta(days=1)
    last_success = _last_success(job) or {}
    seen = job.get("scheduled_through", "")
    pending = job.setdefault("pending_periods", {})
    target = latest
    while target >= enabled_at:
        period = _report_period(name, target)
        if period <= seen:
            break
        archive = kb / "reports" / name / f"{period}.md"
        if (name == "weekly" or target.weekday() < 5) and period != last_success.get("period") and not archive.is_file():
            pending[period] = utc_iso(target)
        target -= step
    job["scheduled_through"] = max(seen, _report_period(name, latest))
    if name == "daily":
        _restore_daily_report_debt(
            kb, pending, settled_period=last_success.get("period"),
            last_attempt=job.get("last_attempt") or {},
            enabled_at=enabled_at, local_now=local_now,
        )


def due_candidate(
    name: str,
    job: Mapping[str, Any],
    *,
    now: datetime,
    local_now: datetime,
    enabled_at: datetime,
) -> dict[str, str] | None:
    schedule = job.get("schedule")
    if not isinstance(schedule, Mapping):
        raise DreamingError(
            "DREAMING_STATE_INVALID",
            f"Dreaming job schedule 缺失: {name}",
        )
    success = job.get("last_recent_success") if name == "process" else _last_success(job)
    kind = schedule.get("kind")
    if kind == "interval":
        minutes = int(schedule.get("minutes", 0))
        baseline = (
            parse_time(success.get("finished_at")) if success is not None else enabled_at
        )
        if minutes <= 0 or baseline is None:
            raise DreamingError(
                "DREAMING_STATE_INVALID",
                f"Dreaming interval 无效: {name}",
            )
        if now < baseline + timedelta(minutes=minutes):
            return None
        ready_since = baseline + timedelta(minutes=minutes)
        return {
            "job": name,
            "period": now.replace(second=0, microsecond=0).strftime("%Y-%m-%dT%H:%MZ"),
            "ready_since": utc_iso(ready_since),
            "deadline_at": "",
        }
    if kind == "daily_time":
        target = _latest_daily_target(
            local_now,
            schedule_time(str(schedule.get("time", ""))),
        )
        if target.astimezone(timezone.utc) < enabled_at:
            return None
        period = target.date().isoformat()
    elif kind == "every_n_days":
        target = _every_n_days_target(
            local_now,
            days=int(schedule.get("days", 0)),
            clock=schedule_time(str(schedule.get("time", ""))),
            anchor_date=str(schedule.get("anchor_date", "")),
        )
        if target.astimezone(timezone.utc) < enabled_at:
            return None
        period = target.date().isoformat()
    elif kind == "weekday_time":
        clock = schedule_time(str(schedule.get("time", "")))
        target = _latest_weekday_target(local_now, clock)
        if target.astimezone(timezone.utc) < enabled_at:
            return None
        period = target.date().isoformat()
    elif kind == "weekly_time":
        target = _latest_weekly_target(
            local_now,
            weekday=int(schedule.get("weekday", -1)),
            clock=schedule_time(str(schedule.get("time", ""))),
        )
        if target.astimezone(timezone.utc) < enabled_at:
            return None
        period = _report_period(name, target)
    else:
        raise DreamingError(
            "DREAMING_STATE_INVALID",
            f"Dreaming schedule kind 无效: {name}",
        )
    if name == "morning" and (target.date() != local_now.date() or local_now.time() >= dreaming_reports.MORNING_CUTOFF):
        return None
    if name in {"daily", "weekly"} and period <= job.get("scheduled_through", ""):
        pending = job["pending_periods"]
        if not pending:
            return None
        period = max(pending)
        target = parse_time(pending[period])
        if target is None:
            raise DreamingError("DREAMING_STATE_INVALID", "待补报告的计划时间无效。")
    if success is not None and success.get("period") == period:
        return None
    return {
        "job": name,
        "period": period,
        "ready_since": utc_iso(target.astimezone(timezone.utc)),
        "deadline_at": utc_iso(target.astimezone(timezone.utc)),
    }


def report_candidate_dependency(kb, state, candidate, current):
    dependency = dreaming_reports.report_dependency_from_state(
        kb, state=state, kind=candidate["job"], period=candidate["period"],
        now=current, scheduled_at=parse_time(candidate["deadline_at"]),
    )
    if candidate["job"] == "daily":
        dependency = dreaming_reports.allow_daily_coverage_gaps(dependency)
        candidate["report_target"] = {
            "period": candidate["period"],
            "scheduled_at": candidate["deadline_at"],
            "through": dependency["window"]["end"],
        }
    return dependency


def next_due_for_job(
    name: str,
    job: Mapping[str, Any],
    *,
    now: datetime,
    local_now: datetime,
    enabled_at: datetime,
) -> dict[str, Any] | None:
    if not job.get("enabled"):
        return None
    if job.get("waiting_for_user"):
        return None
    if name == "morning":
        clock = schedule_time(job["schedule"]["time"])
        if clock >= dreaming_reports.MORNING_CUTOFF:
            return None
        retry = parse_time(job.get("next_attempt_at"))
        earliest = max(local_now, retry.astimezone(local_now.tzinfo)) if retry else local_now
        target = datetime.combine(earliest.date(), clock, local_now.tzinfo)
        if earliest.time() >= dreaming_reports.MORNING_CUTOFF:
            target += timedelta(days=1)
        while target.weekday() >= 5:
            target += timedelta(days=1)
        if target > local_now:
            return {"at": utc_iso(max(target, earliest)), "due": False}
    blocked_by = job.get("blocked_by")
    if isinstance(blocked_by, list) and blocked_by:
        return None
    next_attempt = parse_time(job.get("next_attempt_at"))
    if next_attempt is not None and next_attempt > now:
        return {"at": utc_iso(next_attempt), "due": False}
    candidate = due_candidate(
        name,
        job,
        now=now,
        local_now=local_now,
        enabled_at=enabled_at,
    )
    if candidate is not None:
        at = candidate.get("deadline_at") or candidate.get("ready_since")
        return {"at": at, "due": True}
    schedule = job.get("schedule")
    if not isinstance(schedule, Mapping):
        return None
    success = job.get("last_recent_success") if name == "process" else _last_success(job)
    kind = schedule.get("kind")
    if kind == "interval":
        baseline = (
            parse_time(success.get("finished_at")) if success is not None else enabled_at
        )
        if baseline is None:
            return None
        target_utc = baseline + timedelta(minutes=int(schedule.get("minutes", 0)))
    elif kind == "daily_time":
        latest = _latest_daily_target(
            local_now,
            schedule_time(str(schedule.get("time", ""))),
        )
        target_utc = (latest + timedelta(days=1)).astimezone(timezone.utc)
    elif kind == "every_n_days":
        days = int(schedule.get("days", 0))
        latest = _every_n_days_target(
            local_now,
            days=days,
            clock=schedule_time(str(schedule.get("time", ""))),
            anchor_date=str(schedule.get("anchor_date", "")),
        )
        target_utc = (latest + timedelta(days=days)).astimezone(timezone.utc)
    elif kind == "weekday_time":
        latest = _latest_weekday_target(
            local_now,
            schedule_time(str(schedule.get("time", ""))),
        )
        future = latest + timedelta(days=1)
        while future.weekday() >= 5:
            future += timedelta(days=1)
        target_utc = future.astimezone(timezone.utc)
    elif kind == "weekly_time":
        latest = _latest_weekly_target(
            local_now,
            weekday=int(schedule.get("weekday", -1)),
            clock=schedule_time(str(schedule.get("time", ""))),
        )
        target_utc = (latest + timedelta(days=7)).astimezone(timezone.utc)
    else:
        return None
    return {"at": utc_iso(target_utc), "due": target_utc <= now}
