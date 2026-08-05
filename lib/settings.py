"""Unified user-facing configuration view for Byteworker.

This module is a facade.  It does not replace the existing sources of truth:

- ``.kbconfig`` still locates the private KB.
- ``sources/*.json`` still owns source refresh profiles.
- ``state/report_automation.json`` still owns legacy report automation state.
- ``state/dreaming/state.json`` still owns Dreaming state.

The facade gives the viewer and CLI a stable, user-friendly shape and routes
updates through existing module APIs.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from dreaming_grants import set_im_grant
from dreaming_scheduler import configure as configure_dreaming
from dreaming_scheduler import status as dreaming_status
from dreaming_state import DreamingError
from report_automation import ReportAutomationError
from report_automation import status as report_automation_status
from source_profile_contract import SourceProfileError
from source_profiles import list_profiles, profile_relative_path, save_profile


SETTINGS_SCHEMA = "byteworker-settings/v1"
SOURCE_LABELS = {
    "aeolus": "风神看板",
    "meego": "飞书项目",
    "feishu_doc": "飞书文档",
    "feishu_base": "多维表格",
    "feishu_chat": "飞书群聊",
    "feishu_wiki": "飞书知识库",
}


class SettingsError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {"code": self.code, "message": str(self)}
        if self.details:
            value["details"] = self.details
        return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _context_summary(kb: Path) -> dict[str, Any]:
    path = kb / "context.md"
    text = _read_text(path)
    timezone_name = ""
    for line in text.splitlines():
        normalized = line.strip()
        if not normalized or "时区" not in normalized:
            continue
        if ":" in normalized:
            timezone_name = normalized.split(":", 1)[1].strip()
            break
        if "：" in normalized:
            timezone_name = normalized.split("：", 1)[1].strip()
            break
    return {
        "path": "context.md",
        "exists": path.is_file(),
        "timezone_hint": timezone_name,
        "editable_in_viewer": False,
    }


def _source_profiles(kb: Path) -> list[dict[str, Any]]:
    profiles = list_profiles(kb)
    result = []
    for profile in profiles:
        routine = profile.get("routine")
        routine = routine if isinstance(routine, Mapping) else {}
        result.append(
            {
                "source_uid": profile["source_uid"],
                "source_type": profile["source_type"],
                "source_label": SOURCE_LABELS.get(
                    profile["source_type"],
                    profile["source_type"],
                ),
                "title": profile["title"],
                "routine": {
                    "enabled": bool(routine.get("enabled")),
                    "cadence": routine.get("cadence") or "",
                },
                "profile_path": str(profile_relative_path(profile)),
            }
        )
    return result


def _safe_status(callable_, kb: Path) -> dict[str, Any]:
    try:
        return callable_(kb)
    except (DreamingError, ReportAutomationError) as exc:
        return {"error": exc.as_dict()}


def settings_view(kb: Path) -> dict[str, Any]:
    """Return a user-facing aggregate of current configuration."""

    dreaming = _safe_status(dreaming_status, kb)
    report = _safe_status(report_automation_status, kb)
    sources = _source_profiles(kb)
    return {
        "schema_version": SETTINGS_SCHEMA,
        "generated_at": _now(),
        "kb": {
            "path": str(kb.expanduser().resolve()),
            "context": _context_summary(kb),
        },
        "dreaming": _public_dreaming(dreaming),
        "report_automation": _public_report_automation(report),
        "sources": sources,
        "capabilities": {
            "viewer_write_api": True,
            "legacy_report_automation_editable": False,
            "legacy_report_automation_note": (
                "旧自动日报/周报依赖宿主定时任务，设置页只展示状态，"
                "不创建或伪造宿主任务。"
            ),
        },
    }


def _public_dreaming(value: Mapping[str, Any]) -> dict[str, Any]:
    if "error" in value:
        return {"available": False, "error": value["error"]}
    grants = value.get("grants") if isinstance(value.get("grants"), Mapping) else {}
    im = grants.get("im") if isinstance(grants.get("im"), Mapping) else {}
    jobs = value.get("jobs") if isinstance(value.get("jobs"), Mapping) else {}
    delivery = (
        value.get("report_delivery")
        if isinstance(value.get("report_delivery"), Mapping)
        else {}
    )
    lark = (
        delivery.get("lark_bot")
        if isinstance(delivery.get("lark_bot"), Mapping)
        else {}
    )
    logging = (
        value.get("logging") if isinstance(value.get("logging"), Mapping) else {}
    )
    return {
        "available": True,
        "enabled": bool(value.get("enabled")),
        "operational": bool(value.get("operational")),
        "timezone": value.get("timezone") or "",
        "harness": value.get("harness") or {},
        "harness_preferences": _public_harness_preferences(
            value.get("harness_preferences")
        ),
        "jobs": {
            name: _public_job(jobs.get(name))
            for name in ("process", "morning", "maintenance", "recovery")
        },
        "im": {
            "mode": im.get("mode", "off"),
            "persist_finding": bool(im.get("persist_finding")),
        },
        "logging": {
            "retention_days": int(logging.get("retention_days", 30)),
        },
        "delivery": {
            "lark_summary_enabled": bool(lark.get("enabled")),
            "lark_recipient_id": lark.get("recipient_id", ""),
        },
    }


def _public_harness_preferences(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    return {
        "wake_interval_minutes": int(raw.get("wake_interval_minutes", 120)),
        "model": raw.get("model", ""),
        "schedule_managed_by_viewer": False,
    }


def _public_job(value: Any) -> dict[str, Any]:
    job = value if isinstance(value, Mapping) else {}
    return {
        "enabled": bool(job.get("configured_enabled", job.get("enabled", False))),
        "running_now": bool(job.get("enabled")),
        "schedule": job.get("schedule") if isinstance(job.get("schedule"), Mapping) else {},
        "next_due_at": job.get("next_due_at"),
    }


def _public_report_automation(value: Mapping[str, Any]) -> dict[str, Any]:
    if "error" in value:
        return {"available": False, "error": value["error"]}
    return {
        "available": True,
        "decision": value.get("decision", "unasked"),
        "timezone": value.get("timezone", ""),
        "daily": _legacy_report_kind(value.get("daily")),
        "weekly": _legacy_report_kind(value.get("weekly")),
        "recovery": _legacy_report_kind(value.get("recovery")),
        "editable_in_viewer": False,
    }


def _legacy_report_kind(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    return {
        "enabled": bool(raw.get("enabled")),
        "schedule": raw.get("schedule", ""),
        "native_task_id": raw.get("native_task_id", ""),
        "last_success": raw.get("last_success"),
    }


def update_settings(kb: Path, patch: Mapping[str, Any]) -> dict[str, Any]:
    """Apply a small settings patch and return the fresh aggregate view."""

    if not isinstance(patch, Mapping):
        raise SettingsError("SETTINGS_PATCH_INVALID", "设置更新必须是 JSON 对象。")
    unsupported = sorted(set(patch) - {"dreaming", "sources"})
    if unsupported:
        raise SettingsError(
            "SETTINGS_PATCH_UNSUPPORTED",
            "这些设置暂不支持在 viewer 中修改。",
            details={"fields": unsupported},
        )
    if "dreaming" in patch:
        _update_dreaming(kb, patch["dreaming"])
    if "sources" in patch:
        _update_sources(kb, patch["sources"])
    return settings_view(kb)


def _update_dreaming(kb: Path, patch: Any) -> None:
    if not isinstance(patch, Mapping):
        raise SettingsError("SETTINGS_PATCH_INVALID", "Dreaming 设置必须是对象。")
    config_kwargs: dict[str, Any] = {}
    if "timezone" in patch:
        config_kwargs["timezone_name"] = str(patch["timezone"]).strip()
    jobs = patch.get("jobs")
    if jobs is not None:
        if not isinstance(jobs, Mapping):
            raise SettingsError("SETTINGS_PATCH_INVALID", "Dreaming jobs 必须是对象。")
        _merge_job_patch(config_kwargs, jobs)
    logging = patch.get("logging")
    if logging is not None:
        if not isinstance(logging, Mapping):
            raise SettingsError("SETTINGS_PATCH_INVALID", "Dreaming 日志设置必须是对象。")
        if "retention_days" in logging:
            config_kwargs["log_retention_days"] = int(logging["retention_days"])
    delivery = patch.get("delivery")
    if delivery is not None:
        if not isinstance(delivery, Mapping):
            raise SettingsError("SETTINGS_PATCH_INVALID", "摘要接收设置必须是对象。")
        if "lark_summary_enabled" in delivery:
            config_kwargs["lark_delivery_enabled"] = bool(
                delivery["lark_summary_enabled"]
            )
        if "lark_recipient_id" in delivery:
            config_kwargs["lark_recipient_id"] = str(
                delivery["lark_recipient_id"]
            ).strip()
    harness_preferences = patch.get("harness_preferences")
    if harness_preferences is not None:
        if not isinstance(harness_preferences, Mapping):
            raise SettingsError("SETTINGS_PATCH_INVALID", "本地任务设置必须是对象。")
        if "wake_interval_minutes" in harness_preferences:
            config_kwargs["harness_wake_interval_minutes"] = int(
                harness_preferences["wake_interval_minutes"]
            )
        if "model" in harness_preferences:
            config_kwargs["harness_model"] = str(
                harness_preferences["model"]
            ).strip()
    if config_kwargs:
        configure_dreaming(kb, **config_kwargs)
    im = patch.get("im")
    if im is not None:
        if not isinstance(im, Mapping):
            raise SettingsError("SETTINGS_PATCH_INVALID", "信息来源权限必须是对象。")
        mode = str(im.get("mode", "off")).strip()
        set_im_grant(
            kb,
            mode=mode,
            persist_finding=bool(im.get("persist_finding", False)),
            acknowledge_all_visible=bool(im.get("acknowledge_all_visible", False)),
        )


def _merge_job_patch(target: dict[str, Any], jobs: Mapping[str, Any]) -> None:
    for name, raw in jobs.items():
        if not isinstance(raw, Mapping):
            raise SettingsError("SETTINGS_PATCH_INVALID", f"{name} job 必须是对象。")
        if name == "process":
            if "enabled" in raw:
                target["process_enabled"] = bool(raw["enabled"])
            schedule = raw.get("schedule")
            if isinstance(schedule, Mapping):
                kind = str(schedule.get("kind", "")).strip()
                if kind:
                    target["process_kind"] = kind
                if "minutes" in schedule:
                    target["process_interval_minutes"] = int(schedule["minutes"])
                if "time" in schedule:
                    target["process_time"] = str(schedule["time"]).strip()
                if "days" in schedule:
                    target["process_every_days"] = int(schedule["days"])
        elif name == "morning":
            if "enabled" in raw:
                target["morning_enabled"] = bool(raw["enabled"])
            schedule = raw.get("schedule")
            if isinstance(schedule, Mapping) and "time" in schedule:
                target["morning_time"] = str(schedule["time"]).strip()
        elif name == "maintenance":
            if "enabled" in raw:
                target["maintenance_enabled"] = bool(raw["enabled"])
            schedule = raw.get("schedule")
            if isinstance(schedule, Mapping) and "time" in schedule:
                target["maintenance_time"] = str(schedule["time"]).strip()
        elif name == "recovery":
            if "enabled" in raw:
                target["recovery_enabled"] = bool(raw["enabled"])
            schedule = raw.get("schedule")
            if isinstance(schedule, Mapping) and "minutes" in schedule:
                target["recovery_interval_minutes"] = int(schedule["minutes"])
        else:
            raise SettingsError(
                "SETTINGS_PATCH_UNSUPPORTED",
                "这个 Dreaming 任务暂不支持在设置页修改。",
                details={"job": name},
            )


def _update_sources(kb: Path, patch: Any) -> None:
    if not isinstance(patch, list):
        raise SettingsError("SETTINGS_PATCH_INVALID", "来源设置必须是数组。")
    by_uid = {profile["source_uid"]: profile for profile in list_profiles(kb)}
    for item in patch:
        if not isinstance(item, Mapping):
            raise SettingsError("SETTINGS_PATCH_INVALID", "来源设置项必须是对象。")
        source_uid = str(item.get("source_uid", "")).strip()
        if source_uid not in by_uid:
            raise SettingsError(
                "SETTINGS_SOURCE_NOT_FOUND",
                "未找到来源配置。",
                details={"source_uid": source_uid},
            )
        profile = copy.deepcopy(by_uid[source_uid])
        routine_patch = item.get("routine")
        if not isinstance(routine_patch, Mapping):
            continue
        enabled = bool(routine_patch.get("enabled", False))
        cadence = str(routine_patch.get("cadence", "")).strip()
        profile["routine"] = {
            "enabled": enabled,
            "cadence": cadence if enabled else None,
        }
        save_profile(kb, profile, skill_root=Path(__file__).resolve().parents[1])


def normalize_settings_error(exc: Exception) -> SettingsError:
    if isinstance(exc, SettingsError):
        return exc
    if isinstance(exc, (DreamingError, ReportAutomationError, SourceProfileError)):
        code = getattr(exc, "code", exc.__class__.__name__)
        details = getattr(exc, "details", {})
        return SettingsError(code, str(exc), details=details)
    return SettingsError("SETTINGS_INTERNAL_ERROR", str(exc))
