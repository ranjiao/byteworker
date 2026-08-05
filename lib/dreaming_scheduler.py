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
from dreaming_run_log import (
    DEFAULT_MAX_FILE_BYTES,
    DEFAULT_RETENTION_DAYS,
    STAGES,
    append_run_event,
    logging_config,
)

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
    return load_state_unlocked(kb, now)


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
                next_due = _next_due(
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
    _clock(clock)
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
        for clock_value in (
            resolved_morning_time,
            resolved_daily_time,
            resolved_weekly_time,
            resolved_maintenance_time,
        ):
            _clock(clock_value)
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
            _clock(morning_time)
            jobs["morning"]["schedule"] = {
                "kind": "weekday_time",
                "time": morning_time,
            }
        if maintenance_time is not None:
            _clock(maintenance_time)
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
        if lark_delivery_enabled is not None:
            lark_delivery["enabled"] = lark_delivery_enabled
        lark_delivery.setdefault("enabled", False)
        lark_delivery.setdefault("recipient_id", "")
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
                    job["configured_enabled"] = bool(
                        job.get("configured_enabled", job.get("enabled"))
                    )
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
    if kind == "daily_time":
        target = _latest_daily_target(
            local_now,
            _clock(str(schedule.get("time", ""))),
        )
        if target.astimezone(timezone.utc) < enabled_at:
            return None
        period = target.date().isoformat()
    elif kind == "every_n_days":
        target = _every_n_days_target(
            local_now,
            days=int(schedule.get("days", 0)),
            clock=_clock(str(schedule.get("time", ""))),
            anchor_date=str(schedule.get("anchor_date", "")),
        )
        if target.astimezone(timezone.utc) < enabled_at:
            return None
        period = target.date().isoformat()
    elif kind == "weekday_time":
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


def _next_due(
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
    blocked_by = job.get("blocked_by")
    if isinstance(blocked_by, list) and blocked_by:
        return None
    next_attempt = _parse_time(job.get("next_attempt_at"))
    if next_attempt is not None and next_attempt > now:
        return {"at": _iso(next_attempt), "due": False}
    candidate = _candidate(
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
    success = _last_success(job)
    kind = schedule.get("kind")
    if kind == "interval":
        baseline = (
            _parse_time(success.get("finished_at")) if success is not None else enabled_at
        )
        if baseline is None:
            return None
        target_utc = baseline + timedelta(minutes=int(schedule.get("minutes", 0)))
    elif kind == "daily_time":
        latest = _latest_daily_target(
            local_now,
            _clock(str(schedule.get("time", ""))),
        )
        target_utc = (latest + timedelta(days=1)).astimezone(timezone.utc)
    elif kind == "every_n_days":
        days = int(schedule.get("days", 0))
        latest = _every_n_days_target(
            local_now,
            days=days,
            clock=_clock(str(schedule.get("time", ""))),
            anchor_date=str(schedule.get("anchor_date", "")),
        )
        target_utc = (latest + timedelta(days=days)).astimezone(timezone.utc)
    elif kind == "weekday_time":
        latest = _latest_weekday_target(
            local_now,
            _clock(str(schedule.get("time", ""))),
        )
        future = latest + timedelta(days=1)
        while future.weekday() >= 5:
            future += timedelta(days=1)
        target_utc = future.astimezone(timezone.utc)
    elif kind == "weekly_time":
        latest = _latest_weekly_target(
            local_now,
            weekday=int(schedule.get("weekday", -1)),
            clock=_clock(str(schedule.get("time", ""))),
        )
        target_utc = (latest + timedelta(days=7)).astimezone(timezone.utc)
    else:
        return None
    return {"at": _iso(target_utc), "due": target_utc <= now}


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


def _expire_lease(kb: Path, value: dict[str, Any], now: datetime) -> None:
    lease = value.get("active_lease")
    if not isinstance(lease, Mapping) or _lease_active(lease, now):
        return
    job_name = str(lease.get("job", ""))
    run_id = _lease_run_id(lease)
    jobs = value.get("jobs")
    job = jobs.get(job_name) if isinstance(jobs, Mapping) else None
    if isinstance(job, dict):
        run = {
            "run_id": run_id,
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
        status="failed",
        error_code="DREAMING_LEASE_EXPIRED",
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
            "stage": "scheduled",
            "last_heartbeat_at": _iso(current),
        }
        if selected.get("dependency"):
            lease["dependency"] = selected["dependency"]
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
    now: datetime | None = None,
) -> dict[str, Any]:
    if stage not in STAGES:
        raise DreamingError("DREAMING_RUN_LOG_INVALID", f"未知 run stage: {stage}")
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
        lease["stage"] = stage
        lease["last_heartbeat_at"] = _iso(current)
        jobs = value.get("jobs")
        job = jobs.get(lease.get("job")) if isinstance(jobs, Mapping) else None
        if isinstance(job, dict) and isinstance(job.get("last_attempt"), dict):
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
            retention_days=log_settings["retention_days"],
            max_file_bytes=log_settings["max_file_bytes"],
            now=current,
        )
    return {
        "run_id": event["run_id"],
        "stage": stage,
        "last_heartbeat_at": event["timestamp"],
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
        run_id = _lease_run_id(lease)
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
        started_at = _parse_time(lease.get("acquired_at"))
        metrics: dict[str, int] = {}
        if started_at is not None:
            metrics["duration_ms"] = max(
                0,
                int((current - started_at).total_seconds() * 1000),
            )
        for key, count in (
            ("item_count", item_count),
            ("finding_count", finding_count),
            ("gap_count", gap_count),
        ):
            if count is not None:
                metrics[key] = count
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
            metrics=metrics,
            retention_days=log_settings["retention_days"],
            max_file_bytes=log_settings["max_file_bytes"],
            now=current,
        )
    return run
