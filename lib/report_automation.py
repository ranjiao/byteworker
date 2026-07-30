"""Local state and execution leases for harness-scheduled reports."""

from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping


STATE_SCHEMA = "byteworker-report-automation/v1"
ONBOARDING_VERSION = 1
PROMPT_VERSION = 1
KINDS = {"daily", "weekly"}
RUN_STATUSES = {"success", "failed"}
DAILY_PERIOD_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
WEEKLY_PERIOD_RE = re.compile(r"^(\d{4})-W(\d{2})$")


class ReportAutomationError(RuntimeError):
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


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


def _state_root(kb: Path) -> Path:
    return kb.resolve() / "state"


def state_path(kb: Path) -> Path:
    return _state_root(kb) / "report_automation.json"


def _lock_path(kb: Path) -> Path:
    return _state_root(kb) / "report_automation.lock"


def _ensure_state_ignored(kb: Path) -> None:
    info_exclude = kb.resolve() / ".git" / "info" / "exclude"
    if not info_exclude.parent.is_dir():
        return
    current = info_exclude.read_text(encoding="utf-8") if info_exclude.exists() else ""
    if any(line.strip() == "/state/" for line in current.splitlines()):
        return
    info_exclude.write_text(
        current + ("" if not current or current.endswith("\n") else "\n") + "/state/\n",
        encoding="utf-8",
    )


@contextmanager
def _state_lock(kb: Path) -> Iterator[None]:
    root = _state_root(kb)
    root.mkdir(parents=True, exist_ok=True)
    _ensure_state_ignored(kb)
    with _lock_path(kb).open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _empty_state(now: datetime) -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA,
        "onboarding_version": ONBOARDING_VERSION,
        "prompt_version": PROMPT_VERSION,
        "decision": "unasked",
        "owner_harness": "",
        "environment": "",
        "timezone": "",
        "daily": {
            "enabled": False,
            "schedule": "",
            "native_task_id": "",
            "last_run": None,
        },
        "weekly": {
            "enabled": False,
            "schedule": "",
            "native_task_id": "",
            "last_run": None,
        },
        "active_lease": None,
        "updated_at": _iso(now),
    }


def _load_unlocked(kb: Path, now: datetime) -> dict[str, Any]:
    path = state_path(kb)
    if not path.is_file():
        return _empty_state(now)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReportAutomationError(
            "REPORT_AUTOMATION_STATE_INVALID",
            f"自动报告状态文件损坏: {path}",
            hint="先备份该文件，再重新运行自动报告设置。",
        ) from exc
    if not isinstance(value, dict) or value.get("schema_version") != STATE_SCHEMA:
        raise ReportAutomationError(
            "REPORT_AUTOMATION_STATE_INVALID",
            f"自动报告状态 schema 不受支持: {path}",
        )
    return value


def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _lease_is_active(value: object, now: datetime) -> bool:
    if not isinstance(value, Mapping):
        return False
    expires_at = _parse_time(value.get("expires_at"))
    return expires_at is not None and expires_at > now


def _validate_period(kind: str, period: str) -> str:
    normalized = period.strip()
    try:
        if kind == "daily" and DAILY_PERIOD_RE.fullmatch(normalized):
            date.fromisoformat(normalized)
            return normalized
        weekly = WEEKLY_PERIOD_RE.fullmatch(normalized) if kind == "weekly" else None
        if weekly:
            date.fromisocalendar(int(weekly.group(1)), int(weekly.group(2)), 1)
            return normalized
    except ValueError:
        pass
    expected = "YYYY-MM-DD" if kind == "daily" else "YYYY-Www"
    raise ReportAutomationError(
        "REPORT_AUTOMATION_LEASE_INVALID",
        f"{kind} period 必须是有效的 {expected}。",
    )


def _validate_report_path(kind: str, value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ReportAutomationError(
            "REPORT_AUTOMATION_RUN_RESULT_INVALID",
            "成功回执必须提供报告路径。",
        )
    candidate = Path(normalized)
    expected_parent = Path("reports") / kind
    if candidate.is_absolute() or ".." in candidate.parts or candidate.parent != expected_parent:
        raise ReportAutomationError(
            "REPORT_AUTOMATION_RUN_RESULT_INVALID",
            f"{kind} 报告路径必须位于 {expected_parent}/。",
        )
    return candidate.as_posix()


def status(kb: Path, *, now: datetime | None = None) -> dict[str, Any]:
    current = now or _now()
    with _state_lock(kb):
        value = _load_unlocked(kb, current)
    lease = value.get("active_lease")
    result = dict(value)
    result["needs_onboarding"] = value.get("decision") == "unasked"
    result["prompt_upgrade_available"] = (
        value.get("decision") == "configured"
        and value.get("prompt_version") != PROMPT_VERSION
    )
    result["lease_active"] = _lease_is_active(lease, current)
    return result


def record_decision(
    kb: Path,
    *,
    decision: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    if decision not in {"prompted", "declined", "deferred"}:
        raise ReportAutomationError(
            "REPORT_AUTOMATION_DECISION_INVALID",
            "decision 必须是 prompted、declined 或 deferred。",
        )
    current = now or _now()
    with _state_lock(kb):
        value = _load_unlocked(kb, current)
        value["decision"] = decision
        value["onboarding_version"] = ONBOARDING_VERSION
        value["updated_at"] = _iso(current)
        _atomic_write(state_path(kb), value)
    return status(kb, now=current)


def configure(
    kb: Path,
    *,
    harness: str,
    timezone_name: str,
    environment: str,
    daily_schedule: str,
    weekly_schedule: str,
    daily_task_id: str = "",
    weekly_task_id: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    if not harness.strip() or not timezone_name.strip():
        raise ReportAutomationError(
            "REPORT_AUTOMATION_CONFIG_INVALID",
            "harness 和 timezone 不能为空。",
        )
    if environment != "local":
        raise ReportAutomationError(
            "REPORT_AUTOMATION_CONFIG_INVALID",
            "byteworker 自动报告只能配置为 local 环境。",
            hint="知识库只存在本地，不能改用云端任务。",
        )
    if not daily_schedule.strip() or not weekly_schedule.strip():
        raise ReportAutomationError(
            "REPORT_AUTOMATION_CONFIG_INVALID",
            "日报和周报 schedule 都不能为空。",
        )
    current = now or _now()
    with _state_lock(kb):
        previous = _load_unlocked(kb, current)
        value = _empty_state(current)
        value.update(
            {
                "decision": "configured",
                "owner_harness": harness.strip(),
                "environment": environment,
                "timezone": timezone_name.strip(),
                "updated_at": _iso(current),
            }
        )
        for kind, schedule, task_id in (
            ("daily", daily_schedule, daily_task_id),
            ("weekly", weekly_schedule, weekly_task_id),
        ):
            previous_kind = previous.get(kind)
            last_run = (
                previous_kind.get("last_run")
                if isinstance(previous_kind, Mapping)
                else None
            )
            value[kind] = {
                "enabled": True,
                "schedule": schedule.strip(),
                "native_task_id": task_id.strip(),
                "last_run": last_run,
            }
        value["active_lease"] = previous.get("active_lease")
        _atomic_write(state_path(kb), value)
    return status(kb, now=current)


def acquire_lease(
    kb: Path,
    *,
    kind: str,
    period: str,
    owner: str,
    lease_seconds: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    if kind not in KINDS or not period.strip() or not owner.strip():
        raise ReportAutomationError(
            "REPORT_AUTOMATION_LEASE_INVALID",
            "kind、period 和 owner 必须完整。",
        )
    if lease_seconds <= 0:
        raise ReportAutomationError(
            "REPORT_AUTOMATION_LEASE_INVALID",
            "lease_seconds 必须大于 0。",
        )
    normalized_period = _validate_period(kind, period)
    current = now or _now()
    with _state_lock(kb):
        value = _load_unlocked(kb, current)
        active = value.get("active_lease")
        if _lease_is_active(active, current):
            raise ReportAutomationError(
                "REPORT_AUTOMATION_BUSY",
                "另一个自动报告或补跑正在处理同一知识库。",
                hint="等待当前租约完成；确认任务已中断时可在过期后重试。",
                details={
                    "kind": active.get("kind"),
                    "period": active.get("period"),
                    "owner": active.get("owner"),
                    "expires_at": active.get("expires_at"),
                },
            )
        token = uuid.uuid4().hex
        lease = {
            "token": token,
            "kind": kind,
            "period": normalized_period,
            "owner": owner.strip(),
            "acquired_at": _iso(current),
            "expires_at": _iso(current + timedelta(seconds=lease_seconds)),
        }
        value["active_lease"] = lease
        value["updated_at"] = _iso(current)
        _atomic_write(state_path(kb), value)
    return lease


def complete_run(
    kb: Path,
    *,
    token: str,
    run_status: str,
    report_path: str = "",
    error_code: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    if run_status not in RUN_STATUSES:
        raise ReportAutomationError(
            "REPORT_AUTOMATION_RUN_STATUS_INVALID",
            "run_status 必须是 success 或 failed。",
        )
    current = now or _now()
    with _state_lock(kb):
        value = _load_unlocked(kb, current)
        lease = value.get("active_lease")
        if not isinstance(lease, Mapping) or lease.get("token") != token:
            raise ReportAutomationError(
                "REPORT_AUTOMATION_LEASE_MISMATCH",
                "自动报告租约不存在或 token 不匹配。",
            )
        kind = str(lease.get("kind", ""))
        if run_status == "failed" and not error_code.strip():
            raise ReportAutomationError(
                "REPORT_AUTOMATION_RUN_RESULT_INVALID",
                "失败回执必须提供稳定错误码。",
            )
        normalized_report_path = (
            _validate_report_path(kind, report_path)
            if run_status == "success"
            else report_path.strip()
        )
        run = {
            "status": run_status,
            "period": lease.get("period", ""),
            "owner": lease.get("owner", ""),
            "finished_at": _iso(current),
            "report_path": normalized_report_path,
            "error_code": error_code.strip(),
        }
        kind_state = value.get(kind)
        if not isinstance(kind_state, dict):
            kind_state = {
                "enabled": False,
                "schedule": "",
                "native_task_id": "",
                "last_run": None,
            }
            value[kind] = kind_state
        kind_state["last_run"] = run
        value["active_lease"] = None
        value["updated_at"] = _iso(current)
        _atomic_write(state_path(kb), value)
    return run
