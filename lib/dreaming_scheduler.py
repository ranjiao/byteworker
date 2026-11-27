"""Deterministic control plane for opt-in Byteworker Dreaming jobs."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from dreaming_reports import MORNING_CUTOFF, morning_cutoff
from dreaming_schedule import (
    due_candidate,
    next_due_for_job,
    queue_report_periods,
    report_candidate_dependency,
    schedule_time,
)
from report_owner import report_owner_lock
from dreaming_run_log import (
    BATCH_ID,
    DEFAULT_MAX_FILE_BYTES,
    DEFAULT_RETENTION_DAYS,
    STAGES,
    append_run_event,
    logging_config,
    run_performance,
)
from dreaming_run_result import RESULT_SCHEMA, read_run_result, save_run_result, validate_maintenance_issues

from dreaming_state import (
    STATE_SCHEMA,
    ROUTINE_WAIT_REASONS,
    DreamingError,
    atomic_write_json,
    build_job,
    empty_state,
    load_state_unlocked,
    parse_time,
    secure_path,
    state_lock,
    state_path,
    utc_iso,
)

JOBS = ("process", "morning", "daily", "weekly", "maintenance", "recovery")
RUN_STATUSES = {"success", "partial", "failed"}
SOURCE_REVIEW_REQUIRED = "SOURCE_REVIEW_REQUIRED"
HUMAN_BLOCKING_ERRORS = {
    "DREAMING_GRANT_REQUIRED",
    "DREAMING_STATE_INVALID",
    "DREAMING_STATE_MIGRATION_FAILED",
    "DIGEST_TXN_ROLLBACK_FAILED",
    "KB_MUTATION_ROLLBACK_ERROR",
}
BACKOFF_MINUTES = 5
BACKOFF_MAX_MINUTES = 240
RUNTIME_NOTICE = (
    "Dreaming 会产生额外网络、模型和本地存储开销。若要按时运行，"
    "本地机器必须保持开机、唤醒、联网，并允许宿主执行本地任务；"
    "休眠或关机期间只能在恢复后补跑。"
)
CAPABILITY_TOUR_VERSION = "byteworker-dreaming-tour/v1"
PROCESS_SCHEDULE_KINDS = {"interval", "daily_time", "every_n_days"}


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
    configured_enabled: bool | None = None,
) -> dict[str, Any]:
    return build_job(
        enabled=enabled,
        schedule=schedule,
        previous=previous,
        configured_enabled=configured_enabled,
    )


def _empty_state(now: datetime) -> dict[str, Any]:
    return empty_state(now)


def _load_unlocked(kb: Path, now: datetime) -> dict[str, Any]:
    state = load_state_unlocked(kb, now)
    lease = state.get("active_lease")
    if isinstance(lease, dict):
        # Older installations can still have a morning run extending past noon.
        _limit_morning_lease(lease, state["timezone"])
    return state


def _limit_morning_lease(lease: dict[str, Any], timezone_name: str) -> None:
    if lease["job"] == "morning":
        cutoff = morning_cutoff(lease["period"], timezone_name)
        expiry = _parse_time(lease.get("expires_at"))
        if expiry is not None and expiry > cutoff:
            lease["expires_at"] = _iso(cutoff)


def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    payload = dict(value)
    if path.name == "state.json" and payload.get("schema_version") == STATE_SCHEMA:
        payload["state_revision"] = int(payload.get("state_revision", 0)) + 1
    atomic_write_json(path, payload)


_state_lock = state_lock


def _log_settings(value: Mapping[str, Any]) -> dict[str, int]:
    raw = value.get("logging")
    raw = raw if isinstance(raw, Mapping) else {}
    return logging_config(
        retention_days=int(raw.get("retention_days", DEFAULT_RETENTION_DAYS)),
        max_file_bytes=int(raw.get("max_file_bytes", DEFAULT_MAX_FILE_BYTES)),
    )


def _lease_active(value: object, now: datetime) -> bool:
    if not isinstance(value, Mapping):
        return False
    expires_at = _parse_time(value.get("expires_at"))
    return expires_at is not None and expires_at > now


def _lease_run_id(lease: Mapping[str, Any]) -> str:
    current = str(lease.get("run_id", "")).strip()
    if current:
        return current
    return (
        f"DR-legacy-{str(lease.get('job', 'unknown'))}-"
        f"{int(lease.get('epoch', 0))}"
    )


def _public_lease(value: object, now: datetime) -> dict[str, Any] | None:
    if not _lease_active(value, now) or not isinstance(value, Mapping):
        return None
    return {
        key: value.get(key)
        for key in (
            "run_id",
            "job",
            "period",
            "owner",
            "epoch",
            "acquired_at",
            "expires_at",
            "stage",
            "last_heartbeat_at",
        )
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
    result["requires_schedule_acknowledgement"] = not bool(
        value.get("schedule_acknowledged_at")
    )
    result["machine_runtime_required"] = bool(value.get("enabled"))
    harness = value.get("harness")
    harness = dict(harness) if isinstance(harness, Mapping) else {}
    result["harness"] = harness
    result["operational"] = bool(
        value.get("enabled") and harness.get("status") == "installed"
    )
    timezone_name = str(value.get("timezone", ""))
    enabled_at = _parse_time(value.get("enabled_at"))
    jobs = result.get("jobs")
    if (
        timezone_name
        and enabled_at is not None
        and isinstance(jobs, Mapping)
    ):
        zone = _timezone(timezone_name)
        normalized_jobs: dict[str, Any] = {}
        for name, raw_job in jobs.items():
            job = dict(raw_job) if isinstance(raw_job, Mapping) else raw_job
            if isinstance(job, dict):
                next_due = next_due_for_job(
                    name,
                    job,
                    now=now,
                    local_now=now.astimezone(zone),
                    enabled_at=enabled_at,
                )
                job["next_due_at"] = next_due["at"] if next_due else None
                job["due"] = bool(next_due and next_due["due"])
            normalized_jobs[name] = job
        result["jobs"] = normalized_jobs
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


def _positive(value: int, field: str) -> int:
    if value <= 0:
        raise DreamingError(
            "DREAMING_CONFIG_INVALID",
            f"{field} 必须大于 0。",
        )
    return value


def _harness_preferences(
    previous: Mapping[str, Any],
    *,
    wake_interval_minutes: int | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    old = previous.get("harness_preferences")
    old = old if isinstance(old, Mapping) else {}
    resolved_interval = (
        wake_interval_minutes
        if wake_interval_minutes is not None
        else int(old.get("wake_interval_minutes", 120))
    )
    if resolved_interval < 5:
        raise DreamingError(
            "DREAMING_CONFIG_INVALID",
            "本地任务唤醒间隔不能短于 5 分钟。",
        )
    resolved_model = (
        str(model).strip() if model is not None else str(old.get("model", "")).strip()
    )
    lowered = resolved_model.lower()
    if (
        len(resolved_model) > 80
        or "\n" in resolved_model
        or "\r" in resolved_model
        or any(token in lowered for token in ("token", "secret", "cookie"))
    ):
        raise DreamingError(
            "DREAMING_CONFIG_INVALID",
            "任务模型名称不能包含换行、凭据词或超过 80 字符。",
        )
    return {
        "wake_interval_minutes": resolved_interval,
        "model": resolved_model,
    }


def _process_schedule(
    *,
    kind: str,
    interval_minutes: int | None,
    at_time: str | None,
    every_days: int | None,
    anchor_date: str | None,
    local_now: datetime,
) -> dict[str, Any]:
    if kind not in PROCESS_SCHEDULE_KINDS:
        raise DreamingError(
            "DREAMING_CONFIG_INVALID",
            "process schedule kind 必须是 interval、daily_time 或 every_n_days。",
        )
    if kind == "interval":
        minutes = _positive(
            interval_minutes if interval_minutes is not None else 120,
            "process_interval_minutes",
        )
        return {"kind": kind, "minutes": minutes}
    clock = at_time or "22:00"
    schedule_time(clock)
    if kind == "daily_time":
        return {"kind": kind, "time": clock}
    days = _positive(every_days if every_days is not None else 2, "process_every_days")
    anchor = anchor_date or local_now.date().isoformat()
    try:
        datetime.strptime(anchor, "%Y-%m-%d")
    except ValueError as exc:
        raise DreamingError(
            "DREAMING_CONFIG_INVALID",
            f"process anchor_date 必须是 YYYY-MM-DD: {anchor}",
        ) from exc
    return {
        "kind": kind,
        "days": days,
        "time": clock,
        "anchor_date": anchor,
    }


def _validated_process_schedule(
    value: Mapping[str, Any],
    *,
    local_now: datetime,
) -> dict[str, Any]:
    return _process_schedule(
        kind=str(value.get("kind", "")),
        interval_minutes=(
            int(value["minutes"]) if isinstance(value.get("minutes"), int) else None
        ),
        at_time=str(value.get("time", "")) or None,
        every_days=(
            int(value["days"]) if isinstance(value.get("days"), int) else None
        ),
        anchor_date=str(value.get("anchor_date", "")) or None,
        local_now=local_now,
    )


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
    acknowledge_schedule: bool = False,
    environment: str = "local",
    process_kind: str | None = None,
    process_interval_minutes: int | None = None,
    process_time: str | None = None,
    process_every_days: int | None = None,
    morning_time: str | None = None,
    daily_time: str | None = None,
    weekly_weekday: int | None = None,
    weekly_time: str | None = None,
    maintenance_time: str | None = None,
    recovery_interval_minutes: int | None = None,
    log_retention_days: int | None = None,
    harness_wake_interval_minutes: int | None = None,
    harness_model: str | None = None,
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
    if not acknowledge_schedule:
        raise DreamingError(
            "DREAMING_SCHEDULE_ACK_REQUIRED",
            "启用 Dreaming 前必须先向用户展示并确认完整运行计划。",
            hint="先运行 dreaming configure/status 核对 process、morning、maintenance、recovery。",
        )
    if not harness.strip() or environment != "local":
        raise DreamingError(
            "DREAMING_CONFIG_INVALID",
            "harness 不能为空，environment 必须为 local。",
        )
    _timezone(timezone_name)
    current = now or _now()
    zone = _timezone(timezone_name)
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
        previous_process = previous_jobs.get("process")
        previous_process_schedule = (
            previous_process.get("schedule")
            if isinstance(previous_process, Mapping)
            and isinstance(previous_process.get("schedule"), Mapping)
            else {"kind": "interval", "minutes": 120}
        )
        def previous_schedule(name: str, fallback: Mapping[str, Any]) -> Mapping[str, Any]:
            raw = previous_jobs.get(name)
            schedule = raw.get("schedule") if isinstance(raw, Mapping) else None
            return schedule if isinstance(schedule, Mapping) else fallback

        morning_schedule = previous_schedule(
            "morning", {"kind": "weekday_time", "time": "08:30"}
        )
        daily_schedule = previous_schedule(
            "daily", {"kind": "weekday_time", "time": "20:30"}
        )
        weekly_schedule = previous_schedule(
            "weekly",
            {"kind": "weekly_time", "weekday": 0, "time": "09:30"},
        )
        maintenance_schedule = previous_schedule(
            "maintenance", {"kind": "weekday_time", "time": "03:30"}
        )
        recovery_schedule = previous_schedule(
            "recovery", {"kind": "interval", "minutes": 240}
        )
        resolved_morning_time = morning_time or str(morning_schedule.get("time", "08:30"))
        resolved_daily_time = daily_time or str(daily_schedule.get("time", "20:30"))
        resolved_weekly_time = weekly_time or str(weekly_schedule.get("time", "09:30"))
        resolved_weekly_weekday = (
            weekly_weekday
            if weekly_weekday is not None
            else int(weekly_schedule.get("weekday", 0))
        )
        resolved_maintenance_time = maintenance_time or str(
            maintenance_schedule.get("time", "03:30")
        )
        resolved_recovery_minutes = (
            recovery_interval_minutes
            if recovery_interval_minutes is not None
            else int(recovery_schedule.get("minutes", 240))
        )
        if morning_time is not None and schedule_time(morning_time) >= MORNING_CUTOFF:
            raise DreamingError("DREAMING_CONFIG_INVALID", "晨报的计划时间必须早于本地 12:00。")
        for clock_value in (
            resolved_morning_time,
            resolved_daily_time,
            resolved_weekly_time,
            resolved_maintenance_time,
        ):
            schedule_time(clock_value)
        if resolved_weekly_weekday not in range(7):
            raise DreamingError(
                "DREAMING_CONFIG_INVALID",
                "weekly_weekday 必须是 0..6，0 表示周一。",
            )
        _positive(resolved_recovery_minutes, "recovery_interval_minutes")
        if (
            process_kind is None
            and process_interval_minutes is None
            and process_time is None
            and process_every_days is None
        ):
            process_schedule = _validated_process_schedule(
                previous_process_schedule,
                local_now=current.astimezone(zone),
            )
        else:
            inferred_kind = process_kind or (
                "interval" if process_interval_minutes is not None else "daily_time"
            )
            process_schedule = _process_schedule(
                kind=inferred_kind,
                interval_minutes=process_interval_minutes,
                at_time=process_time,
                every_days=process_every_days,
                anchor_date=None,
                local_now=current.astimezone(zone),
            )
        previous_logging = previous.get("logging")
        previous_logging = (
            previous_logging if isinstance(previous_logging, Mapping) else {}
        )
        log_config = logging_config(
            retention_days=(
                log_retention_days
                if log_retention_days is not None
                else int(
                    previous_logging.get(
                        "retention_days",
                        DEFAULT_RETENTION_DAYS,
                    )
                )
            ),
            max_file_bytes=int(
                previous_logging.get("max_file_bytes", DEFAULT_MAX_FILE_BYTES)
            ),
        )
        value = _empty_state(current)
        if "maintenance_issues" in previous:
            value["maintenance_issues"] = previous["maintenance_issues"]
        for field in (
            "grants",
            "runs",
            "cursors",
            "gaps",
            "receipt_index",
            "source_checkpoints",
            "actions",
            "report_owner",
            "outbox",
            "report_dependencies",
            "foreground_sessions",
        ):
            previous_value = previous.get(field)
            if isinstance(previous_value, Mapping):
                value[field] = dict(previous_value)
        previous_harness = previous.get("harness")
        if (
            isinstance(previous_harness, Mapping)
            and previous.get("owner_harness") == harness.strip()
        ):
            harness_state = dict(previous_harness)
        else:
            harness_state = {
                "status": "pending",
                "task_id": "",
                "registered_at": None,
                "last_tick_at": None,
            }
        value.update(
            {
                "enabled": True,
                "enabled_at": _iso(current),
                "disabled_at": None,
                "owner_harness": harness.strip(),
                "harness": harness_state,
                "environment": environment,
                "timezone": timezone_name,
                "runtime_notice_acknowledged_at": _iso(current),
                "capability_tour_version": CAPABILITY_TOUR_VERSION,
                "capability_tour_acknowledged_at": _iso(current),
                "schedule_acknowledged_at": _iso(current),
                "manage_reports": False,
                "scheduler_owner": "dreaming",
                "migration_epoch": int(previous.get("migration_epoch", 0)) + 1,
                "state_revision": int(previous.get("state_revision", 0)),
                "logging": log_config,
                "harness_preferences": _harness_preferences(
                    previous,
                    wake_interval_minutes=harness_wake_interval_minutes,
                    model=harness_model,
                ),
                "updated_at": _iso(current),
            }
        )
        default_specs = {
            "process": (
                True,
                process_schedule,
            ),
            "morning": (
                True,
                {"kind": "weekday_time", "time": resolved_morning_time},
            ),
            "daily": (
                False,
                {"kind": "weekday_time", "time": resolved_daily_time},
            ),
            "weekly": (
                False,
                {
                    "kind": "weekly_time",
                    "weekday": resolved_weekly_weekday,
                    "time": resolved_weekly_time,
                },
            ),
            "maintenance": (
                True,
                {"kind": "weekday_time", "time": resolved_maintenance_time},
            ),
            "recovery": (
                True,
                {"kind": "interval", "minutes": resolved_recovery_minutes},
            ),
        }
        specs = {}
        for name, (default_enabled, schedule) in default_specs.items():
            old = previous_jobs.get(name)
            configured_enabled = (
                bool(old.get("configured_enabled"))
                if isinstance(old, Mapping)
                and "configured_enabled" in old
                else default_enabled
            )
            specs[name] = (configured_enabled, schedule)
        value["jobs"] = {
            name: _job(
                enabled=enabled_value,
                schedule=schedule,
                previous=(
                    previous_jobs.get(name)
                    if isinstance(previous_jobs.get(name), Mapping)
                    else None
                ),
                configured_enabled=enabled_value,
            )
            for name, (enabled_value, schedule) in specs.items()
        }
        active = previous.get("active_lease")
        value["active_lease"] = active if _lease_active(active, current) else None
        _atomic_write(state_path(kb), value)
    return status(kb, now=current)


def configure(
    kb: Path,
    *,
    timezone_name: str | None = None,
    process_kind: str | None = None,
    process_interval_minutes: int | None = None,
    process_time: str | None = None,
    process_every_days: int | None = None,
    process_enabled: bool | None = None,
    morning_time: str | None = None,
    morning_enabled: bool | None = None,
    maintenance_time: str | None = None,
    maintenance_enabled: bool | None = None,
    recovery_interval_minutes: int | None = None,
    recovery_enabled: bool | None = None,
    log_retention_days: int | None = None,
    lark_delivery_enabled: bool | None = None,
    lark_recipient_id: str | None = None,
    lark_recipient_key: str | None = None,
    harness_wake_interval_minutes: int | None = None,
    harness_model: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or _now()
    with _state_lock(kb):
        value = _load_unlocked(kb, current)
        if _lease_active(value.get("active_lease"), current):
            raise DreamingError(
                "DREAMING_BUSY",
                "Dreaming job 正在运行，不能修改配置。",
            )
        resolved_timezone = timezone_name or str(value.get("timezone", ""))
        if not resolved_timezone:
            raise DreamingError(
                "DREAMING_CONFIG_INVALID",
                "首次配置必须提供 timezone。",
            )
        zone = _timezone(resolved_timezone)
        jobs = value.get("jobs")
        if not isinstance(jobs, dict):
            raise DreamingError("DREAMING_STATE_INVALID", "Dreaming jobs 状态缺失。")
        process = jobs.get("process")
        if not isinstance(process, dict):
            raise DreamingError("DREAMING_STATE_INVALID", "Dreaming process job 缺失。")
        if any(
            item is not None
            for item in (
                process_kind,
                process_interval_minutes,
                process_time,
                process_every_days,
            )
        ):
            inferred_kind = process_kind or (
                "interval"
                if process_interval_minutes is not None
                else "every_n_days"
                if process_every_days is not None
                else "daily_time"
            )
            process["schedule"] = _process_schedule(
                kind=inferred_kind,
                interval_minutes=process_interval_minutes,
                at_time=process_time,
                every_days=process_every_days,
                anchor_date=None,
                local_now=current.astimezone(zone),
            )
            process["ready_since"] = None
            process["deadline_at"] = None
        requested_enabled = {
            "process": process_enabled,
            "morning": morning_enabled,
            "maintenance": maintenance_enabled,
            "recovery": recovery_enabled,
        }
        for name, requested in requested_enabled.items():
            job = jobs.get(name)
            if not isinstance(job, dict):
                raise DreamingError(
                    "DREAMING_STATE_INVALID",
                    f"Dreaming job 缺失: {name}",
                )
            if requested is not None:
                job["configured_enabled"] = requested
                job["enabled"] = bool(value.get("enabled")) and requested
        if morning_time is not None:
            if schedule_time(morning_time) >= MORNING_CUTOFF:
                raise DreamingError("DREAMING_CONFIG_INVALID", "晨报的计划时间必须早于本地 12:00。")
            jobs["morning"]["schedule"] = {
                "kind": "weekday_time",
                "time": morning_time,
            }
        if maintenance_time is not None:
            schedule_time(maintenance_time)
            jobs["maintenance"]["schedule"] = {
                "kind": "weekday_time",
                "time": maintenance_time,
            }
        if recovery_interval_minutes is not None:
            jobs["recovery"]["schedule"] = {
                "kind": "interval",
                "minutes": _positive(
                    recovery_interval_minutes,
                    "recovery_interval_minutes",
                ),
            }
        current_logging = value.get("logging")
        current_logging = (
            dict(current_logging) if isinstance(current_logging, Mapping) else {}
        )
        value["logging"] = logging_config(
            retention_days=(
                log_retention_days
                if log_retention_days is not None
                else int(
                    current_logging.get(
                        "retention_days",
                        DEFAULT_RETENTION_DAYS,
                    )
                )
            ),
            max_file_bytes=int(
                current_logging.get("max_file_bytes", DEFAULT_MAX_FILE_BYTES)
            ),
        )
        delivery = value.get("report_delivery")
        delivery = dict(delivery) if isinstance(delivery, Mapping) else {}
        lark_delivery = delivery.get("lark_bot")
        lark_delivery = (
            dict(lark_delivery) if isinstance(lark_delivery, Mapping) else {}
        )
        if lark_recipient_id is not None:
            recipient = lark_recipient_id.strip()
            if recipient and not recipient.startswith("ou_"):
                raise DreamingError(
                    "DREAMING_CONFIG_INVALID",
                    "飞书摘要收件人必须是 open_id。",
                )
            lark_delivery["recipient_id"] = recipient
            if lark_recipient_key is None:
                lark_delivery["recipient_key"] = recipient
        if lark_recipient_key is not None:
            lark_delivery["recipient_key"] = lark_recipient_key.strip()
        if lark_delivery_enabled is not None:
            lark_delivery["enabled"] = lark_delivery_enabled
        lark_delivery.setdefault("enabled", False)
        lark_delivery.setdefault("recipient_id", "")
        lark_delivery.setdefault(
            "recipient_key",
            lark_delivery["recipient_id"],
        )
        if lark_delivery["enabled"] and not lark_delivery["recipient_id"].startswith(
            "ou_"
        ):
            raise DreamingError(
                "DREAMING_CONFIG_INVALID",
                "启用飞书摘要前必须配置收件人。",
            )
        delivery["host"] = {"enabled": True}
        delivery["lark_bot"] = lark_delivery
        value["report_delivery"] = delivery
        value["harness_preferences"] = _harness_preferences(
            value,
            wake_interval_minutes=harness_wake_interval_minutes,
            model=harness_model,
        )
        value["timezone"] = resolved_timezone
        value["updated_at"] = _iso(current)
        _atomic_write(state_path(kb), value)
    return status(kb, now=current)


def register_harness(
    kb: Path,
    *,
    task_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not task_id.strip():
        raise DreamingError("DREAMING_CONFIG_INVALID", "task_id 不能为空。")
    current = now or _now()
    with _state_lock(kb):
        value = _load_unlocked(kb, current)
        if not value.get("enabled"):
            raise DreamingError("DREAMING_DISABLED", "Dreaming 尚未启用。")
        value["harness"] = {
            "status": "installed",
            "task_id": task_id.strip(),
            "registered_at": _iso(current),
            "last_tick_at": None,
        }
        value["updated_at"] = _iso(current)
        _atomic_write(state_path(kb), value)
    return status(kb, now=current)


def unregister_harness(
    kb: Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or _now()
    with _state_lock(kb):
        value = _load_unlocked(kb, current)
        if _lease_active(value.get("active_lease"), current):
            raise DreamingError(
                "DREAMING_BUSY",
                "Dreaming job 正在运行，不能注销 harness。",
            )
        value["harness"] = {
            "status": "pending",
            "task_id": "",
            "registered_at": None,
            "last_tick_at": None,
        }
        value["updated_at"] = _iso(current)
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
            jobs[name]["configured_enabled"] = enabled
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
                jobs[name].pop("scheduled_through", None)
                jobs[name].pop("pending_periods", None)
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
        value.pop("interrupted_process", None)
        value["active_lease"] = None
        jobs = value.get("jobs")
        if isinstance(jobs, dict):
            for job in jobs.values():
                if isinstance(job, dict):
                    job["configured_enabled"] = bool(
                        job.get("configured_enabled", job.get("enabled"))
                    )
                    job["enabled"] = False
        value["updated_at"] = _iso(current)
        _atomic_write(state_path(kb), value)
    return status(kb, now=current)


def _job_gate(job: Mapping[str, Any], now: datetime) -> str:
    if not job.get("enabled"):
        return "disabled"
    if job.get("waiting_for_user"):
        return "waiting_for_user"
    next_attempt = _parse_time(job.get("next_attempt_at"))
    if next_attempt is not None and next_attempt > now:
        return "backoff"
    blocked_by = job.get("blocked_by")
    if isinstance(blocked_by, list) and blocked_by:
        return "blocked_by_dependency"
    return ""


def _candidate_key(candidate: Mapping[str, Any], now: datetime) -> tuple[Any, ...]:
    deadline = _parse_time(candidate.get("deadline_at"))
    overdue = deadline is not None and deadline <= now
    ready_since = _parse_time(candidate.get("ready_since")) or now
    return (
        0 if candidate.get("recent_report") else 1,
        0 if candidate.get("job") == "daily" and candidate.get("recent_report") else 1,
        -1 if candidate.get("prioritize_dependency") or candidate.get("recent_digest") else 0,
        0 if overdue else 1,
        deadline or datetime.max.replace(tzinfo=timezone.utc),
        ready_since,
        str(candidate.get("job", "")),
    )


def _expire_lease(kb: Path, value: dict[str, Any], now: datetime) -> None:
    lease = value.get("active_lease")
    if not isinstance(lease, Mapping) or _lease_active(lease, now):
        return
    job_name = str(lease.get("job", ""))
    run_id = _lease_run_id(lease)
    jobs = value.get("jobs")
    job = jobs.get(job_name) if isinstance(jobs, Mapping) else None
    if job_name == "process":
        from dreaming_digest import routine_batch_path

        resume_from = run_id if routine_batch_path(kb, run_id).is_file() else lease.get("resume_from")
        value["interrupted_process"] = {
            "period": lease["period"],
            "target_acquired_at": lease.get("target_acquired_at", lease["acquired_at"]),
            "resume_from": resume_from,
            "work_id": lease.get("work_id") or run_id,
            **{key: lease[key] for key in ("dependency", "report_target", "recent_start") if key in lease},
        }
    morning_ended = job_name == "morning" and (
        _parse_time(lease.get("expires_at")) == morning_cutoff(lease["period"], value["timezone"])
    )
    status = "skipped" if morning_ended else "failed"
    error_code = "DREAMING_MORNING_CUTOFF" if morning_ended else "DREAMING_LEASE_EXPIRED"
    if isinstance(job, dict):
        run = {
            "run_id": run_id,
            "status": status,
            "period": lease.get("period", ""),
            "owner": lease.get("owner", ""),
            "epoch": lease.get("epoch"),
            "started_at": lease.get("acquired_at", ""),
            "finished_at": _iso(now),
            "artifact_path": "",
            "coverage_checkpoint": "",
            "error_code": error_code,
        }
        job["last_attempt"] = run
        job["last_run"] = run
    log_settings = _log_settings(value)
    append_run_event(
        kb,
        run_id=run_id,
        event="lease_expired",
        job=job_name,
        period=str(lease.get("period", "")),
        owner=str(lease.get("owner", "")),
        epoch=int(lease.get("epoch", 0)),
        stage=str(lease.get("stage", "scheduled")),
        status=status,
        error_code=error_code,
        retention_days=log_settings["retention_days"],
        max_file_bytes=log_settings["max_file_bytes"],
        now=now,
    )
    value["active_lease"] = None


def run_due(
    kb: Path,
    *,
    owner: str,
    lease_seconds: int = 7200,
    followup_after_run_id: str = "",
    observation: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not owner.strip() or lease_seconds <= 0:
        raise DreamingError(
            "DREAMING_RUN_INVALID",
            "owner 不能为空，lease_seconds 必须大于 0。",
        )
    current = now or _now()
    decisions = observation.setdefault("jobs", {}) if observation is not None else None
    from dreaming_grants import expire_foreground_sessions
    from dreaming_collection import IM_BACKFILL_LOOKBACK, next_process_im_window, plan_im_recovery
    from dreaming_reports import REPORT_KINDS, expire_report_dependencies

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
        harness = value.get("harness")
        harness_tick = bool(
            isinstance(harness, dict)
            and harness.get("status") == "installed"
            and harness.get("task_id") == owner.strip()
        )
        if harness_tick:
            harness["last_tick_at"] = _iso(current)
        if _lease_active(value.get("active_lease"), current):
            if harness_tick:
                value["updated_at"] = _iso(current)
                _atomic_write(state_path(kb), value)
            return {
                "schema_version": STATE_SCHEMA,
                "status": "busy",
                "active_lease": _public_lease(value.get("active_lease"), current),
                "lease": None,
            }
        _expire_lease(kb, value, current)
        expire_report_dependencies(value, current)
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
        followup_run_id = followup_after_run_id.strip()
        report_target = None
        if followup_run_id:
            process = jobs.get("process")
            last_success = (
                process.get("last_success")
                if isinstance(process, Mapping)
                and isinstance(process.get("last_success"), Mapping)
                else None
            )
            if (
                not isinstance(last_success, Mapping)
                or last_success.get("run_id") != followup_run_id
                or (process.get("last_attempt") or {}).get("run_id") != followup_run_id
                or last_success.get("status") != "success"
                or not str(last_success.get("period", "")).startswith("catchup:")
                or str(last_success.get("owner", "")) != owner.strip()
            ):
                value["updated_at"] = _iso(current)
                _atomic_write(state_path(kb), value)
                return {
                    "schema_version": STATE_SCHEMA,
                    "status": "idle",
                    "lease": None,
                }
            report_target = last_success.get("report_target")
        local_now = current.astimezone(zone)
        yesterday = (local_now - timedelta(days=1)).date()
        recent_start = datetime.combine(yesterday, time.min, zone)
        recent_periods = {yesterday.isoformat(), local_now.date().isoformat()}
        recent_due = False
        candidates: list[dict[str, Any]] = []
        recent_blockers: list[dict[str, Any]] = []
        history_blockers: list[dict[str, Any]] = []
        recovery = None
        for name in JOBS:
            if followup_run_id and name not in REPORT_KINDS:
                continue
            if report_target and name != "daily":
                continue
            job = jobs.get(name)
            if not isinstance(job, Mapping):
                raise DreamingError(
                    "DREAMING_STATE_INVALID",
                    f"Dreaming job 状态缺失: {name}",
                )
            # Report blockers describe the last evaluated window. Check the schedule
            # first, then derive fresh blockers for its latest due period.
            if not job.get("enabled"):
                if decisions is not None:
                    decisions[name] = "disabled"
                continue
            if name in {"daily", "weekly"}:
                migrated = _parse_time(value.get("report_owner", {}).get("migrated_at"))
                queue_report_periods(kb, name, job, local_now, max(enabled_at, migrated or enabled_at))
            candidate_job = {**job, "blocked_by": []} if name in REPORT_KINDS else job
            if report_target:
                target_period = report_target["period"]
                pending = job.get("pending_periods", {})
                if target_period not in pending:
                    continue
                candidate_job["pending_periods"] = {target_period: pending[target_period]}
            candidate = due_candidate(
                name,
                candidate_job,
                now=current,
                local_now=local_now,
                enabled_at=enabled_at,
            )
            gate = _job_gate(candidate_job, current)
            if decisions is not None:
                decisions[name] = gate or ("due" if candidate else "not_due")
            if candidate is not None:
                recent_report = (
                    name == "daily" and candidate["period"] in recent_periods
                    or name == "morning" and candidate["period"] == local_now.date().isoformat()
                )
                if name == "process" or recent_report:
                    recent_due = True
                if name == "process":
                    candidate["recent_digest"] = True
                if gate:
                    continue
                if recent_report:
                    candidate["recent_report"] = True
                if name == "recovery":
                    recovery = plan_im_recovery(value, current)
                    window = recovery["window"]
                    if window and _parse_time(window["end"]) > recent_start:
                        recent_due = True
                        window["start"] = _iso(max(_parse_time(window["start"]), recent_start))
                    candidate["historical"] = bool(window and _parse_time(window["end"]) <= recent_start)
                if name in REPORT_KINDS:
                    candidate["historical"] = not recent_report
                    coverage_at = _parse_time((report_target or {}).get("through")) or current
                    dependency = report_candidate_dependency(kb, value, candidate, coverage_at)
                    dependency_key = f"{name}:{candidate['period']}"
                    dependencies = value["report_dependencies"]
                    for key in list(dependencies):
                        if key.startswith(f"{name}:") and key != dependency_key:
                            del dependencies[key]
                    if dependency["status"] == "blocked":
                        blockers = dependency["blockers"]
                        job["blocked_by"] = [item["key"] for item in blockers]
                        dependencies[dependency_key] = dependency
                        if name == "daily":
                            # A remaining daily blocker is a damaged source inventory.
                            if decisions is not None:
                                decisions[name] = "blocked_by_dependency"
                            continue
                        if recent_report:
                            recent_blockers.extend(blockers)
                        else:
                            # Keep the report pending if its only missing evidence
                            # is outside automatic IM recovery.
                            history_blockers.extend(
                                item for item in blockers if item.get("source") != "im"
                                or _parse_time(item["end"]) > current - IM_BACKFILL_LOOKBACK
                            )
                        if decisions is not None:
                            decisions[name] = "blocked_by_dependency"
                        continue
                    job["blocked_by"] = []
                    dependencies.pop(dependency_key, None)
                candidates.append(candidate)
                if not job.get("ready_since"):
                    job["ready_since"] = candidate["ready_since"]
                if candidate.get("deadline_at") and not job.get("deadline_at"):
                    job["deadline_at"] = candidate["deadline_at"]
        if recent_due:
            candidates = [item for item in candidates if not item.get("historical")]
        report_blockers = recent_blockers if recent_due else history_blockers
        if report_blockers and (not followup_run_id or report_target):
            process = jobs.get("process")
            if (isinstance(process, Mapping) and not _job_gate(process, current)
                    and not _job_gate({**process, **process.get("catchup_retry", {})}, current)):
                blocker = min(report_blockers, key=lambda item: (recent_due and not item.get("report_target"), item["key"]))
                digest = hashlib.sha256(blocker["key"].encode("utf-8")).hexdigest()[:16]
                period = f"catchup:{digest}"
                last_attempt = process.get("last_attempt")
                retry = isinstance(last_attempt, Mapping) and last_attempt.get("period") == period
                # A repeat attempt on the same report must yield to a due ordinary digest.
                if not retry:
                    candidates = [item for item in candidates if item["job"] != "process"]
                candidates.append(
                    {
                        "job": "process",
                        "period": period,
                        "prioritize_dependency": not retry,
                        "ready_since": (last_attempt.get("finished_at") if retry else None) or _iso(current),
                        "deadline_at": "",
                        "dependency": blocker,
                        "historical": not recent_due,
                    }
                )
        interrupted = value.get("interrupted_process")
        process_job = jobs.get("process")
        if interrupted and not followup_run_id and not _job_gate(process_job, current):
            candidates = [item for item in candidates if item["job"] != "process"]
            candidates.append({
                "job": "process", "period": interrupted["period"], "recent_digest": True,
                "ready_since": interrupted["target_acquired_at"], "deadline_at": "",
                "resume": interrupted,
            })
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
        if decisions is not None:
            decisions[job_name] = "selected"
        job = jobs[job_name]
        epoch = int(job.get("lease_epoch", 0)) + 1
        token = uuid.uuid4().hex
        run_id = f"DR-{uuid.uuid4().hex}"
        lease = {
            "token": token,
            "run_id": run_id,
            "job": job_name,
            "period": selected["period"],
            "owner": owner.strip(),
            "epoch": epoch,
            "acquired_at": _iso(current),
            "expires_at": _iso(current + timedelta(seconds=lease_seconds)),
            "lease_seconds": lease_seconds,
            "stage": "scheduled",
            "last_heartbeat_at": _iso(current),
        }
        _limit_morning_lease(lease, timezone_name)
        if job_name == "daily":
            lease["report_target"] = selected["report_target"]
        if selected.get("dependency"):
            lease["dependency"] = selected["dependency"]
        if job_name == "recovery":
            lease["im_recovery"] = recovery
        dep = lease.get("dependency") or {}
        if dep.get("report_target"):
            lease["report_target"] = dep["report_target"]
            start = _parse_time(dep["start"])
            # Keep yesterday in the recent scan when it also has an outstanding daily.
            if selected.get("historical") is False and yesterday.isoformat() in jobs["daily"].get("pending_periods", {}):
                start = min(start, recent_start)
            lease["recent_start"] = _iso(start)
        elif job_name == "process" and not selected.get("historical"):
            lease["recent_start"] = _iso(recent_start)
        if job_name == "process":
            lease["work_id"] = run_id
            if selected.get("resume"):
                lease.pop("recent_start", None)
                lease.update(selected["resume"])
                value.pop("interrupted_process", None)
            lease["im_window"] = next_process_im_window(value, lease)
        job["lease_epoch"] = epoch
        job["last_attempt"] = {
            "run_id": run_id,
            "status": "running",
            "period": selected["period"],
            "owner": owner.strip(),
            "epoch": epoch,
            "started_at": lease["acquired_at"],
            "lease_expires_at": lease["expires_at"],
            "stage": "scheduled",
            "last_heartbeat_at": _iso(current),
        }
        job["ready_since"] = selected["ready_since"]
        job["deadline_at"] = selected.get("deadline_at") or None
        value["active_lease"] = lease
        value["updated_at"] = _iso(current)
        _atomic_write(state_path(kb), value)
        if observation is not None:
            observation.update(run_id=run_id, job=job_name)
        log_settings = _log_settings(value)
        append_run_event(
            kb,
            run_id=run_id,
            event="leased",
            job=job_name,
            period=str(selected["period"]),
            owner=owner.strip(),
            epoch=epoch,
            stage="scheduled",
            status="running",
            retention_days=log_settings["retention_days"],
            max_file_bytes=log_settings["max_file_bytes"],
            now=current,
        )
    return {
        "schema_version": STATE_SCHEMA,
        "status": "leased",
        "job": job_name,
        "period": selected["period"],
        "lease": lease,
        "dependency": selected.get("dependency"),
    }


def start_manual_report(
    kb: Path, *, kind: str, period: str, owner: str,
    accepted_source_gaps: list[str] | None = None, gap_note: str = "",
    deliver: bool = False, lease_seconds: int = 7200,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Claim an explicit report period; automatic coverage rules stay unchanged."""
    from dreaming_reports import apply_manual_source_gaps, report_dependency_from_state

    current = now or _now()
    accepted = list(dict.fromkeys(accepted_source_gaps or []))
    if kind not in {"daily", "weekly"} or not owner.strip() or lease_seconds <= 0:
        raise DreamingError("DREAMING_REPORT_INVALID", "手动补报须指定日报或周报、owner 和有效时限。")
    if accepted and not gap_note.strip():
        raise DreamingError("DREAMING_REPORT_INVALID", "接受来源缺口时必须提供限制说明。")
    with report_owner_lock(kb), _state_lock(kb):
        value = _load_unlocked(kb, current)
        if not value["enabled"] or not value["jobs"][kind]["enabled"]:
            raise DreamingError("DREAMING_REPORT_DISABLED", "此报告尚未由 Dreaming 管理。")
        if _report_owner_conflict(kb):
            raise DreamingError("DREAMING_REPORT_OWNER_CONFLICT", "旧报告任务仍持有报告管理权。")
        if _lease_active(value.get("active_lease"), current):
            raise DreamingError("DREAMING_LEASE_BUSY", "已有任务正在执行。")
        dependency = report_dependency_from_state(
            kb, state=value, kind=kind, period=period, now=current,
        )
        if _parse_time(dependency["window"]["start"]) > current:
            raise DreamingError("DREAMING_REPORT_INVALID", "不能生成未来周期的报告。")
        available = {
            item.get("source_key") for item in dependency["blockers"]
            if item.get("source") == "routine_digest" and not item.get("error_code")
        }
        if set(accepted) - available:
            raise DreamingError("DREAMING_REPORT_INVALID", "接受的来源必须是当前周期的明确缺口。")
        manual = {
            "accepted_source_gaps": accepted, "gap_note": gap_note.strip(),
            "deliver": deliver,
        }
        dependency = apply_manual_source_gaps(dependency, {"manual_report": manual})
        if dependency["blockers"]:
            raise DreamingError(
                "DREAMING_REPORT_BLOCKED", "仍有未接受的来源缺口。",
                details={"blockers": dependency["blockers"]},
            )
        _expire_lease(kb, value, current)
        job = value["jobs"][kind]
        epoch = int(job.get("lease_epoch", 0)) + 1
        lease = {
            "token": uuid.uuid4().hex, "run_id": f"DR-{uuid.uuid4().hex}",
            "job": kind, "period": period, "owner": owner.strip(), "epoch": epoch,
            "acquired_at": _iso(current),
            "expires_at": _iso(current + timedelta(seconds=lease_seconds)),
            "stage": "scheduled", "last_heartbeat_at": _iso(current),
            "manual_report": manual,
        }
        job["lease_epoch"] = epoch
        job["last_attempt"] = {
            key: lease[key] for key in ("run_id", "period", "owner", "epoch")
        }
        job["last_attempt"].update(
            status="running", started_at=lease["acquired_at"],
            lease_expires_at=lease["expires_at"], stage="scheduled",
        )
        value["active_lease"] = lease
        value["updated_at"] = _iso(current)
        _atomic_write(state_path(kb), value)
        settings = _log_settings(value)
        append_run_event(
            kb, run_id=lease["run_id"], event="leased", job=kind, period=period,
            owner=owner.strip(), epoch=epoch, stage="scheduled", status="running",
            retention_days=settings["retention_days"],
            max_file_bytes=settings["max_file_bytes"], now=current,
        )
    return {"status": "leased", "job": kind, "period": period,
            "lease": lease, "coverage": dependency}


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
        lease["lease_seconds"] = lease_seconds
        _limit_morning_lease(lease, value["timezone"])
        lease["last_heartbeat_at"] = _iso(current)
        jobs = value.get("jobs")
        job = jobs.get(lease.get("job")) if isinstance(jobs, Mapping) else None
        if isinstance(job, dict) and isinstance(job.get("last_attempt"), dict):
            job["last_attempt"]["lease_expires_at"] = lease["expires_at"]
            job["last_attempt"]["last_heartbeat_at"] = _iso(current)
        value["updated_at"] = _iso(current)
        _atomic_write(state_path(kb), value)
        log_settings = _log_settings(value)
        append_run_event(
            kb,
            run_id=_lease_run_id(lease),
            event="renewed",
            job=str(lease.get("job", "")),
            period=str(lease.get("period", "")),
            owner=str(lease.get("owner", "")),
            epoch=int(lease.get("epoch", 0)),
            stage=str(lease.get("stage", "scheduled")),
            status="running",
            retention_days=log_settings["retention_days"],
            max_file_bytes=log_settings["max_file_bytes"],
            now=current,
        )
    return _public_lease(lease, current) or {}


def heartbeat_run(
    kb: Path,
    *,
    token: str,
    stage: str,
    detail_code: str = "",
    progress_current: int | None = None,
    progress_total: int | None = None,
    digest_run_id: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    if stage not in STAGES:
        raise DreamingError("DREAMING_RUN_LOG_INVALID", f"未知 run stage: {stage}")
    if digest_run_id:
        from operation_trace import DIGEST_RUN_ID
        if not DIGEST_RUN_ID.fullmatch(digest_run_id):
            raise DreamingError("DREAMING_RUN_LOG_INVALID", "Digest 运行编号无效。")
    current = now or _now()
    metrics: dict[str, int] = {}
    if progress_current is not None:
        metrics["progress_current"] = progress_current
    if progress_total is not None:
        metrics["progress_total"] = progress_total
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
        if lease["job"] == "process":
            original = _parse_time(lease["expires_at"]) - _parse_time(lease["acquired_at"])
            duration = timedelta(seconds=lease["lease_seconds"]) if lease.get("lease_seconds") else original
            lease["expires_at"] = _iso(max(_parse_time(lease["expires_at"]), current + duration))
        lease["stage"] = stage
        lease["last_heartbeat_at"] = _iso(current)
        jobs = value.get("jobs")
        job = jobs.get(lease.get("job")) if isinstance(jobs, Mapping) else None
        if isinstance(job, dict) and isinstance(job.get("last_attempt"), dict):
            job["last_attempt"]["lease_expires_at"] = lease["expires_at"]
            job["last_attempt"]["stage"] = stage
            job["last_attempt"]["last_heartbeat_at"] = _iso(current)
        value["updated_at"] = _iso(current)
        _atomic_write(state_path(kb), value)
        log_settings = _log_settings(value)
        event = append_run_event(
            kb,
            run_id=_lease_run_id(lease),
            event="heartbeat",
            job=str(lease.get("job", "")),
            period=str(lease.get("period", "")),
            owner=str(lease.get("owner", "")),
            epoch=int(lease.get("epoch", 0)),
            stage=stage,
            status="running",
            detail_code=detail_code,
            metrics=metrics,
            digest_run_id=digest_run_id,
            retention_days=log_settings["retention_days"],
            max_file_bytes=log_settings["max_file_bytes"],
            now=current,
        )
    return {
        "run_id": event["run_id"],
        "stage": stage,
        "last_heartbeat_at": event["timestamp"],
        "expires_at": lease["expires_at"],
        "metrics": event["metrics"],
    }


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
        job.pop("catchup_retry", None)
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
    item_count: int | None = None,
    finding_count: int | None = None,
    gap_count: int | None = None,
    batch_id: str = "",
    result_document: Mapping[str, Any] | None = None,
    report_sources: list[dict[str, Any]] | None = None,
    publish_report: Callable[[Mapping[str, Any], Mapping[str, Any]], dict[str, Any]] | None = None,
    stage_report_delivery: Callable[[dict[str, Any], Mapping[str, Any], str, str, datetime], None] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if run_status not in RUN_STATUSES:
        raise DreamingError(
            "DREAMING_RUN_STATUS_INVALID",
            "run_status 必须是 success、partial 或 failed。",
        )
    error_code = error_code.strip()
    if run_status != "success" and not error_code:
        raise DreamingError(
            "DREAMING_RUN_RESULT_INVALID",
            "partial/failed 回执必须提供稳定错误码。",
        )
    if any(
        value is not None and value < 0
        for value in (item_count, finding_count, gap_count)
    ):
        raise DreamingError(
            "DREAMING_RUN_RESULT_INVALID",
            "item/finding/gap count 必须是非负整数。",
        )
    current = now or _now()
    with _state_lock(kb):
        value = _load_unlocked(kb, current)
        lease = value.get("active_lease")
        if not _lease_active(lease, current) or lease.get("token") != token:
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
        has_review = "review" in (result_document or {})
        if error_code == SOURCE_REVIEW_REQUIRED:
            if job_name != "process" or run_status == "success" or not has_review:
                raise DreamingError(
                    "DREAMING_RUN_RESULT_INVALID", "待复核的 process 必须提供包含问题和选项的 review。"
                )
        elif has_review:
            raise DreamingError("DREAMING_RUN_RESULT_INVALID", "review 只接受待复核的 process 结果。")
        maintenance_delta = {}
        if result_document is not None and "issues" in result_document:
            if job_name != "maintenance" or run_status != "success":
                raise DreamingError(
                    "DREAMING_RUN_RESULT_INVALID", "issues 只接受完成扫描的 maintenance 回执。"
                )
            issues = validate_maintenance_issues(result_document["issues"])
            previous_issues = validate_maintenance_issues(value.get("maintenance_issues", []))
            previous_ids = {issue["id"] for issue in previous_issues}
            active_ids = {issue["id"] for issue in issues}
            new = [issue for issue in issues if issue["id"] not in previous_ids]
            maintenance_delta = {
                "pending_count": len(issues),
                "new_count": len(new),
                "new": new[:10],
                "resolved_count": len(previous_ids - active_ids),
            }
            value["maintenance_issues"] = issues
        normalized_batch_id = batch_id.strip()
        if job_name == "process":
            from dreaming_digest import process_has_complete_routine_digest, require_routine_digest_completion

            target = lease.get("report_target")
            if run_status == "success" and target:
                from dreaming_reports import report_dependency_from_state

                coverage = report_dependency_from_state(
                    kb, state=value, kind="daily", period=target["period"],
                    now=_parse_time(target.get("through")) or current,
                    scheduled_at=_parse_time(target["scheduled_at"]),
                )
                if coverage["blockers"]:
                    raise DreamingError(
                        "DREAMING_REPORT_BLOCKED", "日报覆盖仍有缺口，请在本轮继续处理。",
                        details={"period": target["period"], "blockers": coverage["blockers"]},
                    )
            elif run_status == "success":
                if not process_has_complete_routine_digest(kb, run_id=_lease_run_id(lease)):
                    raise DreamingError(
                        "DREAMING_DIGEST_INCOMPLETE",
                        "process 成功前必须完成全部已启用来源的普通 digest。",
                    )
                from dreaming_collection import next_process_im_window

                missing = next_process_im_window(value, lease)
                if missing:
                    raise DreamingError(
                        "DREAMING_IM_INCOMPLETE", "消息仍有缺口，请从下一段继续采集。",
                        details={"window": missing},
                    )
            elif error_code != SOURCE_REVIEW_REQUIRED and not (
                run_status == "failed" and error_code in HUMAN_BLOCKING_ERRORS | {
                    "USER_REQUESTED_STOP", "ENOSPC", "COORDINATOR_STATE_WRITE_VIOLATION",
                }
            ):
                require_routine_digest_completion(kb, lease)
        if normalized_batch_id:
            manifest = secure_path(
                kb,
                "batches",
                normalized_batch_id,
                "manifest.json",
            )
            try:
                manifest_value = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                manifest_value = {}
            created_at = (
                _parse_time(manifest_value.get("created_at"))
                if isinstance(manifest_value, Mapping)
                else None
            )
            acquired_at = _parse_time(lease.get("acquired_at"))
            if (
                job_name not in {"process", "recovery"}
                or not BATCH_ID.fullmatch(normalized_batch_id)
                or not manifest.is_file()
                or manifest_value.get("batch_id") != normalized_batch_id
                or created_at is None
                or acquired_at is None
                or created_at < acquired_at
                or created_at > current
            ):
                raise DreamingError(
                    "DREAMING_RUN_RESULT_INVALID",
                    "batch_id 只接受当前采集或恢复任务产出的 EvidenceBatch。",
                )
            if job_name == "recovery":
                planned = (lease.get("im_recovery") or {}).get("window") or {}
                window = manifest_value.get("window") or {}
                lane = {"monitored": "monitored", "all_visible": "discovery"}.get(value["grants"]["im"]["mode"])
                start = _parse_time(window.get("requested_start"))
                if (not planned or not lane or not start or manifest_value.get("source", {}).get("lane") != lane
                        or window.get("requested_end") != planned["end"]
                        or not _parse_time(planned["start"]) <= start < _parse_time(planned["end"])):
                    raise DreamingError("DREAMING_RUN_RESULT_INVALID", "消息批次与本次恢复的来源或时段不符。")
        artifact = _validate_artifact(job_name, artifact_path)
        run_id = _lease_run_id(lease)
        period = str(lease.get("period", ""))
        if job_name in {"morning", "daily", "weekly"} and run_status == "success" and publish_report is None:
            raise DreamingError("DREAMING_RUN_RESULT_INVALID", "报告成功须通过 dreaming report complete 归档。")
        if publish_report is not None:
            if (
                not _lease_active(lease, current)
                or job_name not in {"morning", "daily", "weekly"} or run_status != "success"
                or result_document is None or result_document.get("job") != job_name
                or result_document.get("period") != period
            ):
                raise DreamingError("DREAMING_LEASE_MISMATCH", "报告类型或周期与当前租约不一致。")
            from dreaming_reports import report_dependency_for_lease

            dependency = report_dependency_for_lease(kb, value, lease, current)
            if dependency["blockers"]:
                raise DreamingError(
                    "DREAMING_REPORT_BLOCKED", "报告来源覆盖在发布前仍有阻塞。",
                    details={"blockers": dependency["blockers"]},
                )
            rendered = publish_report(dependency, lease)
            artifact = _validate_artifact(job_name, str(rendered["report_path"]))
            report_sources = rendered["source_distribution"]
            if stage_report_delivery is not None:
                stage_report_delivery(value, lease, artifact, run_id, current)
        started_at = _parse_time(lease.get("acquired_at"))
        metrics = {
            key: count for key, count in (
                ("item_count", item_count), ("finding_count", finding_count), ("gap_count", gap_count),
            ) if count is not None
        }
        if started_at is not None:
            metrics["duration_ms"] = max(0, int((current - started_at).total_seconds() * 1000))
        accounting = {"status": run_status, "error_code": error_code, "metrics": metrics}
        if job_name == "process":
            from dreaming_digest_completion import routine_run_accounting
            from dreaming_action_ledger import run_kb_action_commits

            action_commits = run_kb_action_commits(
                kb, value["actions"], lease_token=token, started_at=started_at,
            )
            accounting.update(routine_run_accounting(kb, run_id, action_commits=action_commits))
            if started_at is not None:
                accounting["performance"] = run_performance(
                    kb, run_id=run_id, started_at=started_at, finished_at=current,
                )
            if action_commits:
                accounting.setdefault("kb_operations", {})["durable_actions"] = len(action_commits)
        if normalized_batch_id:
            accounting["message_batch"] = normalized_batch_id
            by_chat = Counter(
                item["chat_id"] for item in manifest_value.get("items", [])
                if isinstance(item, Mapping) and isinstance(item.get("chat_id"), str)
            )
            accounting["im_sources"] = [
                {"label": f"会话 {index}", "item_count": count}
                for index, count in enumerate(by_chat.values(), start=1)
            ]
        if report_sources is not None:
            accounting["report_sources"] = report_sources
            accounting["kb_operations"] = {"reports_written": 1}
        if result_document is not None and isinstance(result_document.get("repairs"), list):
            accounting["reported_repairs"] = len(result_document["repairs"])
        document = result_document if result_document is not None else {
            "schema_version": RESULT_SCHEMA, "job": job_name, "period": period,
            "summary": "本轮未记录主要信息摘要。",
        }
        result_path = save_run_result(
            kb, document=document, job=job_name, period=period, run_id=run_id,
            now=current, accounting=accounting,
        )
        run = {
            "run_id": run_id,
            "status": run_status,
            "period": lease.get("period", ""),
            "owner": lease.get("owner", ""),
            "epoch": lease.get("epoch"),
            "started_at": lease.get("acquired_at", ""),
            "finished_at": _iso(current),
            "artifact_path": artifact,
            "coverage_checkpoint": coverage_checkpoint.strip(),
            "error_code": error_code,
            "batch_id": normalized_batch_id,
            "result_path": result_path,
        }
        if maintenance_delta:
            run["maintenance"] = maintenance_delta
        if lease.get("report_target"):
            run["report_target"] = lease["report_target"]
        if lease.get("manual_report"):
            run["manual_report"] = lease["manual_report"]
        job["last_attempt"] = run
        job["last_run"] = run
        catchup = job_name == "process" and period.startswith("catchup:")
        retry_state = job.setdefault("catchup_retry", {}) if catchup else job
        if run_status == "success":
            if job_name in {"daily", "weekly"}:
                job.get("pending_periods", {}).pop(period, None)
            job["last_success"] = run
            if job_name == "process" and not catchup:
                job["last_recent_success"] = {"period": period, "finished_at": run["finished_at"]}
            retry_state["consecutive_failures"] = 0
            retry_state["next_attempt_at"] = None
            job["waiting_for_user"] = None
            job["ready_since"] = None
            job["deadline_at"] = None
        else:
            failures = int(retry_state.get("consecutive_failures", 0)) + 1
            retry_state["consecutive_failures"] = failures
            if error_code in HUMAN_BLOCKING_ERRORS:
                job["waiting_for_user"] = error_code
                retry_state["next_attempt_at"] = None
            else:
                routine_gap = error_code in ROUTINE_WAIT_REASONS or (
                    job_name == "process" and error_code == "DREAMING_DIGEST_INCOMPLETE"
                )
                delay = BACKOFF_MINUTES if routine_gap else min(
                    BACKOFF_MAX_MINUTES, BACKOFF_MINUTES * (2 ** (failures - 1)),
                )
                job["waiting_for_user"] = None
                retry_state["next_attempt_at"] = _iso(
                    current + timedelta(minutes=delay)
                )
        value["active_lease"] = None
        value["updated_at"] = _iso(current)
        _atomic_write(state_path(kb), value)
        log_settings = _log_settings(value)
        append_run_event(
            kb,
            run_id=run_id,
            event="completed",
            job=job_name,
            period=str(lease.get("period", "")),
            owner=str(lease.get("owner", "")),
            epoch=int(lease.get("epoch", 0)),
            stage="complete",
            status=run_status,
            error_code=error_code,
            artifact_path=artifact,
            batch_id=normalized_batch_id,
            result_path=result_path,
            metrics=metrics,
            retention_days=log_settings["retention_days"],
            max_file_bytes=log_settings["max_file_bytes"],
            now=current,
        )
    return {**run, "summary": read_run_result(kb, run_id)}
