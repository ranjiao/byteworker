"""Deterministic control plane for opt-in Byteworker Dreaming jobs."""

from __future__ import annotations

import json
import hashlib
import uuid
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from report_owner import report_owner_lock

from dreaming_state import (
    STATE_SCHEMA,
    DreamingError,
    atomic_write_json,
    build_job,
    empty_state,
    load_state_unlocked,
    parse_time,
    state_lock,
    state_path,
    utc_iso,
)

JOBS = ("process", "morning", "daily", "weekly", "maintenance", "recovery")
RUN_STATUSES = {"success", "partial", "failed"}
HUMAN_BLOCKING_ERRORS = {
    "SOURCE_AUTH_REQUIRED",
    "SOURCE_PERMISSION_DENIED",
    "DREAMING_GRANT_REQUIRED",
    "DOCTOR_USER_DECISION_REQUIRED",
}
BACKOFF_MINUTES = 5
BACKOFF_MAX_MINUTES = 240
RUNTIME_NOTICE = (
    "Dreaming 会产生额外网络、模型和本地存储开销。若要按时运行，"
    "本地机器必须保持开机、唤醒、联网，并允许宿主执行本地任务；"
    "休眠或关机期间只能在恢复后补跑。"
)
CAPABILITY_TOUR_VERSION = "byteworker-dreaming-tour/v1"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return utc_iso(value)


def _parse_time(value: object) -> datetime | None:
    return parse_time(value)


def _job(
    *,
    enabled: bool,
    schedule: Mapping[str, Any],
    previous: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return build_job(enabled=enabled, schedule=schedule, previous=previous)


def _empty_state(now: datetime) -> dict[str, Any]:
    return empty_state(now)


def _load_unlocked(kb: Path, now: datetime) -> dict[str, Any]:
    return load_state_unlocked(kb, now)


def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    payload = dict(value)
    if path.name == "state.json" and payload.get("schema_version") == STATE_SCHEMA:
        payload["state_revision"] = int(payload.get("state_revision", 0)) + 1
    atomic_write_json(path, payload)


_state_lock = state_lock


def _lease_active(value: object, now: datetime) -> bool:
    if not isinstance(value, Mapping):
        return False
    expires_at = _parse_time(value.get("expires_at"))
    return expires_at is not None and expires_at > now


def _public_lease(value: object, now: datetime) -> dict[str, Any] | None:
    if not _lease_active(value, now) or not isinstance(value, Mapping):
        return None
    return {
        key: value.get(key)
        for key in ("job", "period", "owner", "epoch", "acquired_at", "expires_at")
    }


def _status_value(value: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    result = dict(value)
    sessions = result.pop("foreground_sessions", {})
    result["foreground_session_count"] = (
        sum(
            1
            for session in sessions.values()
            if isinstance(session, Mapping) and session.get("status") == "active"
        )
        if isinstance(sessions, Mapping)
        else 0
    )
    result["active_lease"] = _public_lease(value.get("active_lease"), now)
    result["runtime_notice"] = RUNTIME_NOTICE
    result["requires_explicit_enable"] = not bool(value.get("enabled"))
    result["requires_capability_tour"] = (
        value.get("capability_tour_version") != CAPABILITY_TOUR_VERSION
    )
    result["machine_runtime_required"] = bool(value.get("enabled"))
    return result


def status(kb: Path, *, now: datetime | None = None) -> dict[str, Any]:
    current = now or _now()
    if not state_path(kb).is_file():
        return _status_value(_empty_state(current), current)
    with _state_lock(kb):
        value = _load_unlocked(kb, current)
    return _status_value(value, current)


def _timezone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise DreamingError(
            "DREAMING_CONFIG_INVALID",
            f"无效 timezone: {value}",
        ) from exc


def _clock(value: str) -> time:
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except ValueError as exc:
        raise DreamingError(
            "DREAMING_CONFIG_INVALID",
            f"时间必须是 HH:MM: {value}",
        ) from exc
    return parsed.time()


def _positive(value: int, field: str) -> int:
    if value <= 0:
        raise DreamingError(
            "DREAMING_CONFIG_INVALID",
            f"{field} 必须大于 0。",
        )
    return value


def _report_owner_conflict(kb: Path) -> bool:
    path = kb.resolve() / "state" / "report_automation.json"
    if not path.is_file():
        return False
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True
    if not isinstance(value, Mapping) or value.get("decision") != "configured":
        return False
    return any(
        isinstance(value.get(kind), Mapping) and value[kind].get("enabled")
        for kind in ("daily", "weekly")
    )


def _legacy_report_state(kb: Path) -> dict[str, Any] | None:
    path = kb.resolve() / "state" / "report_automation.json"
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DreamingError(
            "DREAMING_REPORT_OWNER_CONFLICT",
            "旧 report automation 状态损坏，拒绝迁移。",
        ) from exc
    return value if isinstance(value, dict) else None


def enable(
    kb: Path,
    *,
    harness: str,
    timezone_name: str,
    acknowledge_machine_runtime: bool,
    acknowledge_capability_tour: bool = False,
    environment: str = "local",
    process_interval_minutes: int = 120,
    morning_time: str = "08:30",
    daily_time: str = "20:30",
    weekly_weekday: int = 0,
    weekly_time: str = "09:30",
    maintenance_time: str = "03:30",
    recovery_interval_minutes: int = 240,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not acknowledge_capability_tour:
        raise DreamingError(
            "DREAMING_CAPABILITY_TOUR_REQUIRED",
            "启用 Dreaming 前必须先向用户完整介绍能力、授权、成本和边界。",
            hint="读取 references/dreaming-onboarding.md，完成导览并取得用户明确确认。",
        )
    if not acknowledge_machine_runtime:
        raise DreamingError(
            "DREAMING_RUNTIME_ACK_REQUIRED",
            "启用 Dreaming 前必须确认机器运行要求和额外开销。",
            hint=RUNTIME_NOTICE,
        )
    if not harness.strip() or environment != "local":
        raise DreamingError(
            "DREAMING_CONFIG_INVALID",
            "harness 不能为空，environment 必须为 local。",
        )
    _timezone(timezone_name)
    _clock(morning_time)
    _clock(daily_time)
    _clock(weekly_time)
    _clock(maintenance_time)
    if weekly_weekday not in range(7):
        raise DreamingError(
            "DREAMING_CONFIG_INVALID",
            "weekly_weekday 必须是 0..6，0 表示周一。",
        )
    _positive(process_interval_minutes, "process_interval_minutes")
    _positive(recovery_interval_minutes, "recovery_interval_minutes")
    current = now or _now()
    with _state_lock(kb):
        previous = _load_unlocked(kb, current)
        if _lease_active(previous.get("active_lease"), current):
            raise DreamingError(
                "DREAMING_BUSY",
                "Dreaming job 正在运行，不能修改启用配置。",
                hint="等待当前 job 完成或租约过期后重试。",
            )
        previous_jobs = previous.get("jobs")
        previous_jobs = previous_jobs if isinstance(previous_jobs, Mapping) else {}
        value = _empty_state(current)
        for field in (
            "grants",
            "runs",
            "cursors",
            "gaps",
            "receipt_index",
            "actions",
            "report_owner",
            "outbox",
            "report_dependencies",
            "foreground_sessions",
        ):
            previous_value = previous.get(field)
            if isinstance(previous_value, Mapping):
                value[field] = dict(previous_value)
        value.update(
            {
                "enabled": True,
                "enabled_at": _iso(current),
                "disabled_at": None,
                "owner_harness": harness.strip(),
                "environment": environment,
                "timezone": timezone_name,
                "runtime_notice_acknowledged_at": _iso(current),
                "capability_tour_version": CAPABILITY_TOUR_VERSION,
                "capability_tour_acknowledged_at": _iso(current),
                "manage_reports": False,
                "scheduler_owner": "dreaming",
                "migration_epoch": int(previous.get("migration_epoch", 0)) + 1,
                "state_revision": int(previous.get("state_revision", 0)),
                "updated_at": _iso(current),
            }
        )
        specs = {
            "process": (
                True,
                {"kind": "interval", "minutes": process_interval_minutes},
            ),
            "morning": (
                True,
                {"kind": "weekday_time", "time": morning_time},
            ),
            "daily": (
                False,
                {"kind": "weekday_time", "time": daily_time},
            ),
            "weekly": (
                False,
                {
                    "kind": "weekly_time",
                    "weekday": weekly_weekday,
                    "time": weekly_time,
                },
            ),
            "maintenance": (
                True,
                {"kind": "weekday_time", "time": maintenance_time},
            ),
            "recovery": (
                True,
                {"kind": "interval", "minutes": recovery_interval_minutes},
            ),
        }
        value["jobs"] = {
            name: _job(
                enabled=enabled_value,
                schedule=schedule,
                previous=(
                    previous_jobs.get(name)
                    if isinstance(previous_jobs.get(name), Mapping)
                    else None
                ),
            )
            for name, (enabled_value, schedule) in specs.items()
        }
        active = previous.get("active_lease")
        value["active_lease"] = active if _lease_active(active, current) else None
        _atomic_write(state_path(kb), value)
    return status(kb, now=current)


def set_report_management(
    kb: Path,
    *,
    enabled: bool,
    acknowledge_owner_released: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or _now()
    with report_owner_lock(kb), _state_lock(kb):
        value = _load_unlocked(kb, current)
        if not value.get("enabled"):
            raise DreamingError(
                "DREAMING_DISABLED",
                "Dreaming 尚未启用。",
            )
        if _lease_active(value.get("active_lease"), current):
            raise DreamingError(
                "DREAMING_BUSY",
                "Dreaming job 正在运行，不能迁移报告 owner。",
                hint="等待当前 job 完成或租约过期后重试。",
            )
        if enabled:
            if not acknowledge_owner_released:
                raise DreamingError(
                    "DREAMING_REPORT_MIGRATION_ACK_REQUIRED",
                    "接管日报/周报前必须确认旧 scheduler owner 已释放。",
                )
            if _report_owner_conflict(kb):
                raise DreamingError(
                    "DREAMING_REPORT_OWNER_CONFLICT",
                    "现有 report automation 仍启用，Dreaming 拒绝重复接管。",
                    hint="先停用宿主中的旧日报/周报任务并完成 owner migration。",
                )
            legacy = _legacy_report_state(kb)
            if (
                legacy is not None
                and legacy.get("decision") == "configured"
                and legacy.get("scheduler_owner") != "released-to-dreaming"
            ):
                raise DreamingError(
                    "DREAMING_REPORT_OWNER_CONFLICT",
                    "旧 report automation 尚未记录 owner release。",
                    hint="先停止旧宿主任务并运行 report-automation release-owner。",
                )
        else:
            legacy = _legacy_report_state(kb)
        jobs = value.get("jobs")
        if not isinstance(jobs, dict):
            raise DreamingError("DREAMING_STATE_INVALID", "Dreaming jobs 状态缺失。")
        for name in ("daily", "weekly"):
            if not isinstance(jobs.get(name), dict):
                raise DreamingError(
                    "DREAMING_STATE_INVALID",
                    f"Dreaming job 状态缺失: {name}",
                )
            jobs[name]["enabled"] = enabled
            if enabled and isinstance(legacy, Mapping):
                legacy_job = legacy.get(name)
                legacy_success = (
                    legacy_job.get("last_success")
                    if isinstance(legacy_job, Mapping)
                    else None
                )
                if (
                    isinstance(legacy_success, Mapping)
                    and legacy_success.get("status") == "success"
                ):
                    jobs[name]["last_success"] = {
                        "status": "success",
                        "period": legacy_success.get("period", ""),
                        "owner": "legacy-report-automation",
                        "epoch": 0,
                        "started_at": legacy_success.get("started_at", ""),
                        "finished_at": legacy_success.get("finished_at", ""),
                        "artifact_path": legacy_success.get("report_path", ""),
                        "coverage_checkpoint": "",
                        "error_code": "",
                    }
            if not enabled:
                jobs[name]["blocked_by"] = []
                for dependency_key in list(value.get("report_dependencies", {})):
                    if dependency_key.startswith(f"{name}:"):
                        value["report_dependencies"].pop(dependency_key, None)
        value["manage_reports"] = enabled
        value["migration_epoch"] = int(value.get("migration_epoch", 0)) + 1
        report_owner = value.get("report_owner")
        report_owner = report_owner if isinstance(report_owner, dict) else {}
        report_owner.update(
            {
                "owner": "dreaming" if enabled else "released",
                "migration_epoch": int(report_owner.get("migration_epoch", 0)) + 1,
                "migrated_at": _iso(current) if enabled else report_owner.get("migrated_at"),
                "legacy_snapshot": legacy,
            }
        )
        value["report_owner"] = report_owner
        value["updated_at"] = _iso(current)
        _atomic_write(state_path(kb), value)
    return status(kb, now=current)


def disable(kb: Path, *, now: datetime | None = None) -> dict[str, Any]:
    current = now or _now()
    with _state_lock(kb):
        value = _load_unlocked(kb, current)
        if _lease_active(value.get("active_lease"), current):
            raise DreamingError(
                "DREAMING_BUSY",
                "Dreaming job 正在运行，不能禁用。",
                hint="等待当前 job 完成或租约过期后重试。",
            )
        value["enabled"] = False
        value["disabled_at"] = _iso(current)
        value["active_lease"] = None
        jobs = value.get("jobs")
        if isinstance(jobs, dict):
            for job in jobs.values():
                if isinstance(job, dict):
                    job["enabled"] = False
        value["updated_at"] = _iso(current)
        _atomic_write(state_path(kb), value)
    return status(kb, now=current)


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


def _candidate(
    name: str,
    job: Mapping[str, Any],
    *,
    now: datetime,
    local_now: datetime,
    enabled_at: datetime,
) -> dict[str, str] | None:
    if not job.get("enabled"):
        return None
    if job.get("waiting_for_user"):
        return None
    next_attempt = _parse_time(job.get("next_attempt_at"))
    if next_attempt is not None and next_attempt > now:
        return None
    blocked_by = job.get("blocked_by")
    if isinstance(blocked_by, list) and blocked_by:
        return None
    schedule = job.get("schedule")
    if not isinstance(schedule, Mapping):
        raise DreamingError(
            "DREAMING_STATE_INVALID",
            f"Dreaming job schedule 缺失: {name}",
        )
    success = _last_success(job)
    kind = schedule.get("kind")
    if kind == "interval":
        minutes = int(schedule.get("minutes", 0))
        baseline = (
            _parse_time(success.get("finished_at")) if success is not None else enabled_at
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
            "ready_since": _iso(ready_since),
            "deadline_at": "",
        }
    if kind == "weekday_time":
        target = _latest_weekday_target(local_now, _clock(str(schedule.get("time", ""))))
        if target.astimezone(timezone.utc) < enabled_at:
            return None
        period = target.date().isoformat()
    elif kind == "weekly_time":
        target = _latest_weekly_target(
            local_now,
            weekday=int(schedule.get("weekday", -1)),
            clock=_clock(str(schedule.get("time", ""))),
        )
        if target.astimezone(timezone.utc) < enabled_at:
            return None
        previous_week = (target.date() - timedelta(days=7)).isocalendar()
        period = f"{previous_week.year}-W{previous_week.week:02d}"
    else:
        raise DreamingError(
            "DREAMING_STATE_INVALID",
            f"Dreaming schedule kind 无效: {name}",
        )
    if success is not None and success.get("period") == period:
        return None
    return {
        "job": name,
        "period": period,
        "ready_since": _iso(target.astimezone(timezone.utc)),
        "deadline_at": _iso(target.astimezone(timezone.utc)),
    }


def _candidate_key(candidate: Mapping[str, Any], now: datetime) -> tuple[Any, ...]:
    deadline = _parse_time(candidate.get("deadline_at"))
    overdue = deadline is not None and deadline <= now
    ready_since = _parse_time(candidate.get("ready_since")) or now
    return (
        -1 if candidate.get("dependency") else 0,
        0 if overdue else 1,
        deadline or datetime.max.replace(tzinfo=timezone.utc),
        ready_since,
        str(candidate.get("job", "")),
    )


def _expire_lease(value: dict[str, Any], now: datetime) -> None:
    lease = value.get("active_lease")
    if not isinstance(lease, Mapping) or _lease_active(lease, now):
        return
    job_name = str(lease.get("job", ""))
    jobs = value.get("jobs")
    job = jobs.get(job_name) if isinstance(jobs, Mapping) else None
    if isinstance(job, dict):
        run = {
            "status": "failed",
            "period": lease.get("period", ""),
            "owner": lease.get("owner", ""),
            "epoch": lease.get("epoch"),
            "started_at": lease.get("acquired_at", ""),
            "finished_at": _iso(now),
            "artifact_path": "",
            "coverage_checkpoint": "",
            "error_code": "DREAMING_LEASE_EXPIRED",
        }
        job["last_attempt"] = run
        job["last_run"] = run
    value["active_lease"] = None


def run_due(
    kb: Path,
    *,
    owner: str,
    lease_seconds: int = 7200,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not owner.strip() or lease_seconds <= 0:
        raise DreamingError(
            "DREAMING_RUN_INVALID",
            "owner 不能为空，lease_seconds 必须大于 0。",
        )
    current = now or _now()
    from dreaming_grants import expire_foreground_sessions

    expire_foreground_sessions(kb, now=current)
    with _state_lock(kb):
        value = _load_unlocked(kb, current)
        if not value.get("enabled"):
            return {
                "schema_version": STATE_SCHEMA,
                "status": "disabled",
                "runtime_notice": RUNTIME_NOTICE,
                "lease": None,
            }
        if _lease_active(value.get("active_lease"), current):
            return {
                "schema_version": STATE_SCHEMA,
                "status": "busy",
                "active_lease": _public_lease(value.get("active_lease"), current),
                "lease": None,
            }
        _expire_lease(value, current)
        timezone_name = str(value.get("timezone", ""))
        zone = _timezone(timezone_name)
        enabled_at = _parse_time(value.get("enabled_at"))
        if enabled_at is None:
            raise DreamingError(
                "DREAMING_STATE_INVALID",
                "Dreaming enabled_at 缺失。",
            )
        jobs = value.get("jobs")
        if not isinstance(jobs, Mapping):
            raise DreamingError("DREAMING_STATE_INVALID", "Dreaming jobs 状态缺失。")
        candidates: list[dict[str, Any]] = []
        report_blockers: list[dict[str, Any]] = []
        for name in JOBS:
            job = jobs.get(name)
            if not isinstance(job, Mapping):
                raise DreamingError(
                    "DREAMING_STATE_INVALID",
                    f"Dreaming job 状态缺失: {name}",
                )
            candidate = _candidate(
                name,
                job,
                now=current,
                local_now=current.astimezone(zone),
                enabled_at=enabled_at,
            )
            if candidate is not None:
                if name in {"morning", "daily", "weekly"}:
                    from dreaming_reports import report_dependency_from_state

                    dependency = report_dependency_from_state(
                        kb,
                        state=value,
                        kind=name,
                        period=candidate["period"],
                        now=_parse_time(candidate.get("deadline_at")) or current,
                    )
                    dependency_key = f"{name}:{candidate['period']}"
                    if dependency["status"] == "blocked":
                        blockers = dependency["blockers"]
                        job["blocked_by"] = [item["key"] for item in blockers]
                        value["report_dependencies"][dependency_key] = dependency
                        report_blockers.extend(blockers)
                        continue
                    job["blocked_by"] = []
                    value["report_dependencies"].pop(dependency_key, None)
                candidates.append(candidate)
                if not job.get("ready_since"):
                    job["ready_since"] = candidate["ready_since"]
                if candidate.get("deadline_at") and not job.get("deadline_at"):
                    job["deadline_at"] = candidate["deadline_at"]
        if report_blockers:
            process = jobs.get("process")
            if isinstance(process, Mapping) and process.get("enabled"):
                next_attempt = _parse_time(process.get("next_attempt_at"))
                can_run = (
                    not process.get("waiting_for_user")
                    and not process.get("blocked_by")
                    and (next_attempt is None or next_attempt <= current)
                )
                if can_run:
                    blocker = sorted(report_blockers, key=lambda item: item["key"])[0]
                    digest = hashlib.sha256(blocker["key"].encode("utf-8")).hexdigest()[:16]
                    candidates = [
                        item for item in candidates if item.get("job") != "process"
                    ]
                    candidates.append(
                        {
                            "job": "process",
                            "period": f"catchup:{digest}",
                            "ready_since": _iso(current),
                            "deadline_at": "",
                            "dependency": blocker,
                        }
                    )
        if not candidates:
            value["updated_at"] = _iso(current)
            _atomic_write(state_path(kb), value)
            return {
                "schema_version": STATE_SCHEMA,
                "status": "idle",
                "lease": None,
            }
        selected = min(candidates, key=lambda item: _candidate_key(item, current))
        job_name = selected["job"]
        job = jobs[job_name]
        epoch = int(job.get("lease_epoch", 0)) + 1
        token = uuid.uuid4().hex
        lease = {
            "token": token,
            "job": job_name,
            "period": selected["period"],
            "owner": owner.strip(),
            "epoch": epoch,
            "acquired_at": _iso(current),
            "expires_at": _iso(current + timedelta(seconds=lease_seconds)),
        }
        if selected.get("dependency"):
            lease["dependency"] = selected["dependency"]
        job["lease_epoch"] = epoch
        job["last_attempt"] = {
            "status": "running",
            "period": selected["period"],
            "owner": owner.strip(),
            "epoch": epoch,
            "started_at": lease["acquired_at"],
            "lease_expires_at": lease["expires_at"],
        }
        job["ready_since"] = selected["ready_since"]
        job["deadline_at"] = selected.get("deadline_at") or None
        value["active_lease"] = lease
        value["updated_at"] = _iso(current)
        _atomic_write(state_path(kb), value)
    return {
        "schema_version": STATE_SCHEMA,
        "status": "leased",
        "job": job_name,
        "period": selected["period"],
        "lease": lease,
        "dependency": selected.get("dependency"),
    }


def renew_lease(
    kb: Path,
    *,
    token: str,
    lease_seconds: int = 7200,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not token.strip() or lease_seconds <= 0:
        raise DreamingError(
            "DREAMING_RUN_INVALID",
            "token 不能为空，lease_seconds 必须大于 0。",
        )
    current = now or _now()
    with _state_lock(kb):
        value = _load_unlocked(kb, current)
        lease = value.get("active_lease")
        if (
            not isinstance(lease, dict)
            or lease.get("token") != token
            or not _lease_active(lease, current)
        ):
            raise DreamingError(
                "DREAMING_LEASE_MISMATCH",
                "Dreaming 租约不存在、已过期或 token 不匹配。",
            )
        lease["expires_at"] = _iso(current + timedelta(seconds=lease_seconds))
        jobs = value.get("jobs")
        job = jobs.get(lease.get("job")) if isinstance(jobs, Mapping) else None
        if isinstance(job, dict) and isinstance(job.get("last_attempt"), dict):
            job["last_attempt"]["lease_expires_at"] = lease["expires_at"]
        value["updated_at"] = _iso(current)
        _atomic_write(state_path(kb), value)
    return _public_lease(lease, current) or {}


def retry_job(
    kb: Path,
    *,
    job_name: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    if job_name not in JOBS:
        raise DreamingError("DREAMING_RUN_INVALID", f"未知 Dreaming job: {job_name}")
    current = now or _now()
    with _state_lock(kb):
        value = _load_unlocked(kb, current)
        if _lease_active(value.get("active_lease"), current):
            raise DreamingError("DREAMING_BUSY", "Dreaming job 正在运行，不能重置重试状态。")
        jobs = value.get("jobs")
        job = jobs.get(job_name) if isinstance(jobs, Mapping) else None
        if not isinstance(job, dict):
            raise DreamingError("DREAMING_STATE_INVALID", f"Dreaming job 缺失: {job_name}")
        job["waiting_for_user"] = None
        job["next_attempt_at"] = None
        job["ready_since"] = None
        value["updated_at"] = _iso(current)
        _atomic_write(state_path(kb), value)
    return {"job": job_name, "status": "retry_enabled"}


def _validate_artifact(job: str, value: str) -> str:
    normalized = value.strip()
    if not normalized:
        return ""
    candidate = Path(normalized)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise DreamingError(
            "DREAMING_RUN_RESULT_INVALID",
            "artifact_path 必须是知识库内相对路径。",
        )
    expected = {
        "morning": Path("reports") / "morning",
        "daily": Path("reports") / "daily",
        "weekly": Path("reports") / "weekly",
    }.get(job)
    if expected is not None and candidate.parent != expected:
        raise DreamingError(
            "DREAMING_RUN_RESULT_INVALID",
            f"{job} artifact_path 必须位于 {expected}/。",
        )
    return candidate.as_posix()


def complete_run(
    kb: Path,
    *,
    token: str,
    run_status: str,
    artifact_path: str = "",
    coverage_checkpoint: str = "",
    error_code: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    if run_status not in RUN_STATUSES:
        raise DreamingError(
            "DREAMING_RUN_STATUS_INVALID",
            "run_status 必须是 success、partial 或 failed。",
        )
    if run_status != "success" and not error_code.strip():
        raise DreamingError(
            "DREAMING_RUN_RESULT_INVALID",
            "partial/failed 回执必须提供稳定错误码。",
        )
    current = now or _now()
    with _state_lock(kb):
        value = _load_unlocked(kb, current)
        lease = value.get("active_lease")
        if not isinstance(lease, Mapping) or lease.get("token") != token:
            raise DreamingError(
                "DREAMING_LEASE_MISMATCH",
                "Dreaming 租约不存在或 token 不匹配。",
            )
        job_name = str(lease.get("job", ""))
        jobs = value.get("jobs")
        job = jobs.get(job_name) if isinstance(jobs, Mapping) else None
        if not isinstance(job, dict) or job.get("lease_epoch") != lease.get("epoch"):
            raise DreamingError(
                "DREAMING_LEASE_MISMATCH",
                "Dreaming lease epoch 已过期。",
            )
        artifact = _validate_artifact(job_name, artifact_path)
        run = {
            "status": run_status,
            "period": lease.get("period", ""),
            "owner": lease.get("owner", ""),
            "epoch": lease.get("epoch"),
            "started_at": lease.get("acquired_at", ""),
            "finished_at": _iso(current),
            "artifact_path": artifact,
            "coverage_checkpoint": coverage_checkpoint.strip(),
            "error_code": error_code.strip(),
        }
        job["last_attempt"] = run
        job["last_run"] = run
        if run_status == "success":
            job["last_success"] = run
            job["consecutive_failures"] = 0
            job["next_attempt_at"] = None
            job["waiting_for_user"] = None
            job["ready_since"] = None
            job["deadline_at"] = None
        else:
            failures = int(job.get("consecutive_failures", 0)) + 1
            job["consecutive_failures"] = failures
            if error_code in HUMAN_BLOCKING_ERRORS:
                job["waiting_for_user"] = error_code
                job["next_attempt_at"] = None
            else:
                delay = min(
                    BACKOFF_MAX_MINUTES,
                    BACKOFF_MINUTES * (2 ** (failures - 1)),
                )
                job["waiting_for_user"] = None
                job["next_attempt_at"] = _iso(
                    current + timedelta(minutes=delay)
                )
        value["active_lease"] = None
        value["updated_at"] = _iso(current)
        _atomic_write(state_path(kb), value)
    return run
