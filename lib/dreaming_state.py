"""Secure local state storage and schema migration for Dreaming."""

from __future__ import annotations

import fcntl
import json
import os
import stat
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping


def _secure_chmod(path: Path, mode: int) -> None:
    """Attempt chmod; if TCC/sandbox blocks it, verify existing mode is already secure."""
    try:
        os.chmod(path, mode)
    except PermissionError:
        try:
            current = stat.S_IMODE(path.stat().st_mode)
        except OSError:
            raise
        if (current & 0o077) != 0:
            raise


def _secure_fchmod(fd: int, mode: int) -> None:
    """Attempt fchmod; if TCC/sandbox blocks it, verify existing mode is already secure."""
    try:
        os.fchmod(fd, mode)
    except PermissionError:
        try:
            current = stat.S_IMODE(os.fstat(fd).st_mode)
        except OSError:
            raise
        if (current & 0o077) != 0:
            raise


STATE_SCHEMA = "byteworker-dreaming/v2"
LEGACY_STATE_SCHEMA = "byteworker-dreaming/v1"
DIRECTORY_MODE = 0o700
FILE_MODE = 0o600
JOB_NAMES = (
    "process",
    "morning",
    "daily",
    "weekly",
    "maintenance",
    "recovery",
)


class DreamingError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        hint: str = "",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint
        self.details = dict(details or {})

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {"code": self.code, "message": str(self)}
        if self.hint:
            value["hint"] = self.hint
        if self.details:
            value["details"] = self.details
        return value


def utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


def state_root(kb: Path) -> Path:
    return kb.expanduser().resolve() / "state" / "dreaming"


def secure_path(kb: Path, *parts: str) -> Path:
    root = state_root(kb)
    candidate = root.joinpath(*parts)
    if any(Path(part).is_absolute() or ".." in Path(part).parts for part in parts):
        raise DreamingError(
            "DREAMING_STATE_PATH_INVALID",
            "Dreaming state 路径必须是 state/dreaming 下的相对路径。",
        )
    resolved = candidate.resolve(strict=False)
    if resolved != root and root not in resolved.parents:
        raise DreamingError(
            "DREAMING_STATE_PATH_INVALID",
            "Dreaming state 路径逃逸。",
        )
    return resolved


def state_path(kb: Path) -> Path:
    return secure_path(kb, "state.json")


def _lock_path(kb: Path) -> Path:
    return secure_path(kb, "state.lock")


def _ensure_directory(path: Path) -> None:
    if path.is_symlink():
        raise DreamingError(
            "DREAMING_STATE_PATH_INVALID",
            f"Dreaming state 目录不能是符号链接: {path}",
        )
    path.mkdir(parents=True, exist_ok=True, mode=DIRECTORY_MODE)
    _secure_chmod(path, DIRECTORY_MODE)


def _ensure_state_ignored(kb: Path) -> None:
    info_exclude = kb.expanduser().resolve() / ".git" / "info" / "exclude"
    if not info_exclude.parent.is_dir():
        return
    current = info_exclude.read_text(encoding="utf-8") if info_exclude.exists() else ""
    if any(line.strip() == "/state/" for line in current.splitlines()):
        return
    info_exclude.write_text(
        current + ("" if not current or current.endswith("\n") else "\n") + "/state/\n",
        encoding="utf-8",
    )


def ensure_layout(kb: Path) -> Path:
    root = state_root(kb)
    state_directory = root.parent
    if state_directory.is_symlink() or root.is_symlink():
        raise DreamingError(
            "DREAMING_STATE_PATH_INVALID",
            "KB state/ 和 state/dreaming/ 不能是符号链接。",
        )
    _ensure_directory(root)
    _ensure_state_ignored(kb)
    return root


def state_usage(kb: Path) -> dict[str, int]:
    root = state_root(kb)
    if not root.exists():
        return {"files": 0, "bytes": 0}
    if root.is_symlink():
        raise DreamingError(
            "DREAMING_STATE_PATH_INVALID",
            "state/dreaming/ 不能是符号链接。",
        )
    files = 0
    size = 0
    for path in root.rglob("*"):
        if path.is_symlink():
            raise DreamingError(
                "DREAMING_STATE_PATH_INVALID",
                f"Dreaming state 内不能包含符号链接: {path}",
            )
        if path.is_file():
            files += 1
            size += path.stat().st_size
    return {"files": files, "bytes": size}


@contextmanager
def state_lock(kb: Path) -> Iterator[None]:
    ensure_layout(kb)
    lock = _lock_path(kb)
    descriptor = os.open(lock, os.O_RDWR | os.O_CREAT, FILE_MODE)
    _secure_chmod(lock, FILE_MODE)
    with os.fdopen(descriptor, "a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    _ensure_directory(path.parent)
    payload = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        _secure_fchmod(fd, FILE_MODE)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _secure_chmod(path, FILE_MODE)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _job(
    *,
    enabled: bool,
    schedule: Mapping[str, Any],
    previous: Mapping[str, Any] | None = None,
    configured_enabled: bool | None = None,
) -> dict[str, Any]:
    old = previous or {}
    return {
        "enabled": enabled,
        "configured_enabled": (
            configured_enabled
            if configured_enabled is not None
            else bool(old.get("configured_enabled", enabled))
        ),
        "schedule": dict(schedule),
        "lease_epoch": int(old.get("lease_epoch", 0)),
        "last_attempt": old.get("last_attempt"),
        "last_run": old.get("last_run"),
        "last_success": old.get("last_success"),
        "next_attempt_at": old.get("next_attempt_at"),
        "consecutive_failures": int(old.get("consecutive_failures", 0)),
        "deadline_at": old.get("deadline_at"),
        "blocked_by": list(old.get("blocked_by", [])),
        "ready_since": old.get("ready_since"),
        "waiting_for_user": old.get("waiting_for_user"),
    }


def empty_state(now: datetime) -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA,
        "enabled": False,
        "enabled_at": None,
        "disabled_at": None,
        "owner_harness": "",
        "harness": {
            "status": "pending",
            "task_id": "",
            "registered_at": None,
            "last_tick_at": None,
        },
        "harness_preferences": {
            "wake_interval_minutes": 120,
            "model": "",
        },
        "environment": "local",
        "timezone": "",
        "runtime_notice_acknowledged_at": None,
        "capability_tour_version": "",
        "capability_tour_acknowledged_at": None,
        "schedule_acknowledged_at": None,
        "manage_reports": False,
        "scheduler_owner": "dreaming",
        "report_owner": {
            "owner": "legacy",
            "migration_epoch": 0,
            "migrated_at": None,
            "legacy_snapshot": None,
        },
        "migration_epoch": 0,
        "state_revision": 0,
        "logging": {
            "retention_days": 30,
            "max_file_bytes": 5 * 1024 * 1024,
        },
        "report_delivery": {
            "host": {"enabled": True},
            "lark_bot": {
                "enabled": False,
                "recipient_id": "",
            },
        },
        "grants": {
            "revision": 0,
            "im": {
                "mode": "off",
                "persist_finding": False,
                "updated_at": None,
            },
            "actions": {
                "persist_report": False,
                "archive": False,
                "instant_alert": False,
                "updated_at": None,
            },
        },
        "jobs": {
            "process": _job(
                enabled=False,
                schedule={"kind": "interval", "minutes": 120},
                configured_enabled=True,
            ),
            "morning": _job(
                enabled=False,
                schedule={"kind": "weekday_time", "time": "08:30"},
                configured_enabled=True,
            ),
            "daily": _job(
                enabled=False,
                schedule={"kind": "weekday_time", "time": "20:30"},
                configured_enabled=False,
            ),
            "weekly": _job(
                enabled=False,
                schedule={"kind": "weekly_time", "weekday": 0, "time": "09:30"},
                configured_enabled=False,
            ),
            "maintenance": _job(
                enabled=False,
                schedule={"kind": "weekday_time", "time": "03:30"},
                configured_enabled=True,
            ),
            "recovery": _job(
                enabled=False,
                schedule={"kind": "interval", "minutes": 240},
                configured_enabled=True,
            ),
        },
        "runs": {},
        "cursors": {},
        "gaps": {},
        "receipt_index": {},
        "actions": {},
        "outbox": {},
        "report_dependencies": {},
        "foreground_sessions": {},
        "active_lease": None,
        "updated_at": utc_iso(now),
    }


def build_job(
    *,
    enabled: bool,
    schedule: Mapping[str, Any],
    previous: Mapping[str, Any] | None = None,
    configured_enabled: bool | None = None,
) -> dict[str, Any]:
    return _job(
        enabled=enabled,
        schedule=schedule,
        previous=previous,
        configured_enabled=configured_enabled,
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DreamingError(
            "DREAMING_STATE_INVALID",
            f"Dreaming 状态文件损坏: {path}",
            hint="先备份状态文件；Dreaming 保持关闭，既有命令不受影响。",
        ) from exc
    if not isinstance(value, dict):
        raise DreamingError(
            "DREAMING_STATE_INVALID",
            f"Dreaming 状态必须是 JSON object: {path}",
        )
    return value


def _validate_v2(value: Mapping[str, Any]) -> dict[str, Any]:
    if value.get("schema_version") != STATE_SCHEMA:
        raise DreamingError(
            "DREAMING_STATE_INVALID",
            "Dreaming v2 schema_version 无效。",
        )
    jobs = value.get("jobs")
    grants = value.get("grants")
    if not isinstance(jobs, Mapping) or not isinstance(grants, Mapping):
        raise DreamingError(
            "DREAMING_STATE_INVALID",
            "Dreaming v2 状态缺少 jobs 或 grants。",
        )
    normalized_jobs = dict(jobs)
    if "maintenance" not in normalized_jobs:
        normalized_jobs["maintenance"] = _job(
            enabled=False,
            schedule={"kind": "weekday_time", "time": "03:30"},
            configured_enabled=True,
        )
    for name in JOB_NAMES:
        job = normalized_jobs.get(name)
        if not isinstance(job, Mapping) or not isinstance(
            job.get("schedule"), Mapping
        ):
            raise DreamingError(
                "DREAMING_STATE_INVALID",
                f"Dreaming v2 job 无效: {name}",
            )
    result = dict(value)
    result["jobs"] = normalized_jobs
    result.setdefault("capability_tour_version", "")
    result.setdefault("capability_tour_acknowledged_at", None)
    result.setdefault("schedule_acknowledged_at", None)
    result.setdefault(
        "harness",
        {
            "status": "pending",
            "task_id": "",
            "registered_at": None,
            "last_tick_at": None,
        },
    )
    result.setdefault(
        "harness_preferences",
        {
            "wake_interval_minutes": 120,
            "model": "",
        },
    )
    result.setdefault(
        "logging",
        {
            "retention_days": 30,
            "max_file_bytes": 5 * 1024 * 1024,
        },
    )
    result.setdefault(
        "report_delivery",
        {
            "host": {"enabled": True},
            "lark_bot": {"enabled": False, "recipient_id": ""},
        },
    )
    harness = result["harness"]
    if (
        not isinstance(harness, Mapping)
        or harness.get("status") not in {"pending", "installed", "error"}
        or not isinstance(harness.get("task_id"), str)
    ):
        raise DreamingError(
            "DREAMING_STATE_INVALID",
            "Dreaming v2 harness 无效。",
        )
    harness_preferences = result["harness_preferences"]
    if (
        not isinstance(harness_preferences, Mapping)
        or not isinstance(harness_preferences.get("wake_interval_minutes"), int)
        or harness_preferences["wake_interval_minutes"] < 5
        or not isinstance(harness_preferences.get("model"), str)
        or len(harness_preferences["model"]) > 80
        or any(
            token in harness_preferences["model"].lower()
            for token in ("token", "secret", "cookie", "\n", "\r")
        )
    ):
        raise DreamingError(
            "DREAMING_STATE_INVALID",
            "Dreaming v2 harness preferences 无效。",
        )
    logging = result["logging"]
    if (
        not isinstance(logging, Mapping)
        or not isinstance(logging.get("retention_days"), int)
        or not 1 <= logging["retention_days"] <= 365
        or not isinstance(logging.get("max_file_bytes"), int)
        or logging["max_file_bytes"] < 1024
    ):
        raise DreamingError(
            "DREAMING_STATE_INVALID",
            "Dreaming v2 logging 无效。",
        )
    delivery = result["report_delivery"]
    host_delivery = delivery.get("host") if isinstance(delivery, Mapping) else None
    lark_delivery = (
        delivery.get("lark_bot") if isinstance(delivery, Mapping) else None
    )
    if (
        not isinstance(host_delivery, Mapping)
        or not isinstance(host_delivery.get("enabled"), bool)
        or not isinstance(lark_delivery, Mapping)
        or not isinstance(lark_delivery.get("enabled"), bool)
        or not isinstance(lark_delivery.get("recipient_id"), str)
        or (
            lark_delivery.get("enabled")
            and not lark_delivery.get("recipient_id", "").startswith("ou_")
        )
    ):
        raise DreamingError(
            "DREAMING_STATE_INVALID",
            "Dreaming v2 report_delivery 无效。",
        )
    normalized_grants = dict(grants)
    im_grant = normalized_grants.get("im")
    if (
        not isinstance(grants.get("revision"), int)
        or not isinstance(im_grant, Mapping)
        or im_grant.get("mode") not in {"off", "monitored", "all_visible"}
        or not isinstance(im_grant.get("persist_finding"), bool)
    ):
        raise DreamingError(
            "DREAMING_STATE_INVALID",
            "Dreaming v2 grants 无效。",
        )
    action_grants = normalized_grants.get("actions")
    if action_grants is None:
        normalized_grants["actions"] = {
            "persist_report": False,
            "archive": False,
            "instant_alert": False,
            "updated_at": None,
        }
    elif (
        not isinstance(action_grants, Mapping)
        or not isinstance(action_grants.get("persist_report"), bool)
        or not isinstance(action_grants.get("archive"), bool)
        or not isinstance(action_grants.get("instant_alert"), bool)
    ):
        raise DreamingError(
            "DREAMING_STATE_INVALID",
            "Dreaming v2 action grants 无效。",
        )
    result["grants"] = normalized_grants
    if "actions" not in result:
        result["actions"] = {}
    result.setdefault("outbox", {})
    result.setdefault("report_dependencies", {})
    result.setdefault("foreground_sessions", {})
    if "report_owner" not in result:
        result["report_owner"] = {
            "owner": "legacy",
            "migration_epoch": 0,
            "migrated_at": None,
            "legacy_snapshot": None,
        }
    if not isinstance(result["report_owner"], Mapping):
        raise DreamingError(
            "DREAMING_STATE_INVALID",
            "Dreaming v2 report_owner 必须是 object。",
        )
    for field in (
        "runs",
        "cursors",
        "gaps",
        "receipt_index",
        "actions",
        "outbox",
        "report_dependencies",
        "foreground_sessions",
    ):
        if not isinstance(result.get(field), Mapping):
            raise DreamingError(
                "DREAMING_STATE_INVALID",
                f"Dreaming v2 {field} 必须是 object。",
            )
    return result


def _migrate_v1(value: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    migrated = empty_state(now)
    for key in (
        "enabled",
        "enabled_at",
        "disabled_at",
        "owner_harness",
        "harness",
        "environment",
        "timezone",
        "runtime_notice_acknowledged_at",
        "capability_tour_version",
        "capability_tour_acknowledged_at",
        "schedule_acknowledged_at",
        "manage_reports",
        "scheduler_owner",
        "migration_epoch",
        "logging",
        "active_lease",
        "updated_at",
    ):
        if key in value:
            migrated[key] = value[key]
    old_jobs = value.get("jobs")
    if not isinstance(old_jobs, Mapping):
        raise DreamingError(
            "DREAMING_STATE_MIGRATION_FAILED",
            "Dreaming v1 状态缺少 jobs。",
        )
    for name in JOB_NAMES:
        old = old_jobs.get(name)
        if name == "maintenance" and not isinstance(old, Mapping):
            continue
        if not isinstance(old, Mapping):
            raise DreamingError(
                "DREAMING_STATE_MIGRATION_FAILED",
                f"Dreaming v1 状态缺少 job: {name}",
            )
        schedule = old.get("schedule")
        if not isinstance(schedule, Mapping):
            raise DreamingError(
                "DREAMING_STATE_MIGRATION_FAILED",
                f"Dreaming v1 job schedule 无效: {name}",
            )
        migrated["jobs"][name] = _job(
            enabled=bool(old.get("enabled")),
            schedule=schedule,
            previous=old,
        )
    migrated["schema_version"] = STATE_SCHEMA
    migrated["state_revision"] = 1
    migrated["migrated_from"] = LEGACY_STATE_SCHEMA
    migrated["migrated_at"] = utc_iso(now)
    return migrated


def _migration_backup_path(kb: Path, now: datetime) -> Path:
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return secure_path(kb, "migrations", f"state-v1-{stamp}.json")


def load_state_unlocked(kb: Path, now: datetime) -> dict[str, Any]:
    path = state_path(kb)
    if not path.is_file():
        return empty_state(now)
    value = _read_json(path)
    schema = value.get("schema_version")
    if schema == STATE_SCHEMA:
        return _validate_v2(value)
    if schema != LEGACY_STATE_SCHEMA:
        raise DreamingError(
            "DREAMING_STATE_INVALID",
            f"Dreaming 状态 schema 不受支持: {schema!r}",
        )
    migrated = _migrate_v1(value, now)
    _validate_v2(migrated)
    backup = _migration_backup_path(kb, now)
    atomic_write_json(backup, value)
    try:
        atomic_write_json(path, migrated)
    except OSError as exc:
        raise DreamingError(
            "DREAMING_STATE_MIGRATION_FAILED",
            "Dreaming v1→v2 迁移写入失败；原状态保持不变。",
            details={"backup_path": str(backup)},
        ) from exc
    return migrated


def save_state_unlocked(kb: Path, value: Mapping[str, Any]) -> None:
    if value.get("schema_version") != STATE_SCHEMA:
        raise DreamingError(
            "DREAMING_STATE_INVALID",
            "拒绝写入非 v2 Dreaming 状态。",
        )
    payload = dict(value)
    payload["state_revision"] = int(payload.get("state_revision", 0)) + 1
    atomic_write_json(state_path(kb), payload)
