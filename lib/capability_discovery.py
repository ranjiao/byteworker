"""Deterministic, local-only capability discovery and tip state."""

from __future__ import annotations

import fcntl
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence


STATE_SCHEMA = "byteworker-capability-discovery/v1"
STATUS_SCHEMA = "byteworker-capability-status/v1"
RECOMMENDATION_SCHEMA = "byteworker-capability-recommendation/v1"
CATALOG_SCHEMA = "byteworker-capability-catalog/v1"
TIP_COOLDOWN = timedelta(days=7)
TIP_SUCCESS_INTERVAL = 5
MAX_CAPABILITY_IMPRESSIONS = 2
VALID_SIGNALS = {"repeated_source", "user_judgment"}
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG_PATH = ROOT / "references" / "capabilities.json"


class CapabilityDiscoveryError(RuntimeError):
    def __init__(self, code: str, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint

    def as_dict(self) -> dict[str, str]:
        result = {"code": self.code, "message": str(self)}
        if self.hint:
            result["hint"] = self.hint
        return result


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


def load_catalog(path: Path | None = None) -> dict[str, Any]:
    target = path or DEFAULT_CATALOG_PATH
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CapabilityDiscoveryError(
            "CAPABILITY_CATALOG_INVALID",
            f"无法读取能力目录: {target}",
        ) from exc
    if not isinstance(value, dict) or value.get("schema_version") != CATALOG_SCHEMA:
        raise CapabilityDiscoveryError(
            "CAPABILITY_CATALOG_INVALID",
            f"能力目录 schema 不受支持: {target}",
        )
    capabilities = value.get("capabilities")
    recommendations = value.get("recommendations")
    if not isinstance(capabilities, list) or not isinstance(recommendations, list):
        raise CapabilityDiscoveryError(
            "CAPABILITY_CATALOG_INVALID",
            "能力目录缺少 capabilities 或 recommendations。",
        )
    ids = [item.get("id") for item in capabilities if isinstance(item, Mapping)]
    if (
        len(ids) != len(capabilities)
        or any(not isinstance(item, str) or not item for item in ids)
        or len(set(ids)) != len(ids)
    ):
        raise CapabilityDiscoveryError(
            "CAPABILITY_CATALOG_INVALID",
            "能力目录包含缺失或重复的 capability id。",
        )
    known = set(ids)
    for item in recommendations:
        if not isinstance(item, Mapping) or item.get("capability_id") not in known:
            raise CapabilityDiscoveryError(
                "CAPABILITY_CATALOG_INVALID",
                "推荐规则引用了未知 capability。",
            )
    return value


def capability_ids(path: Path | None = None) -> tuple[str, ...]:
    return tuple(item["id"] for item in load_catalog(path)["capabilities"])


def _state_root(kb: Path) -> Path:
    return kb.resolve() / "state"


def state_path(kb: Path) -> Path:
    return _state_root(kb) / "capability_discovery.json"


def _lock_path(kb: Path) -> Path:
    return _state_root(kb) / "capability_discovery.lock"


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
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    _ensure_state_ignored(kb)
    lock_path = _lock_path(kb)
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            os.chmod(lock_path, 0o600)
        except OSError:
            pass
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _empty_state(now: datetime) -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA,
        "tips_enabled": True,
        "successful_uses": 0,
        "successful_uses_since_tip": 0,
        "last_tip_at": None,
        "events": {},
        "capabilities": {},
        "updated_at": _iso(now),
    }


def _load_unlocked(kb: Path, now: datetime) -> dict[str, Any]:
    path = state_path(kb)
    if not path.is_file():
        return _empty_state(now)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CapabilityDiscoveryError(
            "CAPABILITY_DISCOVERY_STATE_INVALID",
            f"能力发现状态损坏: {path}",
            hint="备份后删除该状态文件，再运行 discover feedback --action enable 重新初始化。",
        ) from exc
    if not isinstance(value, dict) or value.get("schema_version") != STATE_SCHEMA:
        raise CapabilityDiscoveryError(
            "CAPABILITY_DISCOVERY_STATE_INVALID",
            f"能力发现状态 schema 不受支持: {path}",
        )
    if (
        not isinstance(value.get("tips_enabled", True), bool)
        or not isinstance(value.get("events", {}), dict)
        or not isinstance(value.get("capabilities", {}), dict)
        or not isinstance(value.get("successful_uses", 0), int)
        or not isinstance(value.get("successful_uses_since_tip", 0), int)
        or any(
            not isinstance(item, Mapping)
            for item in value.get("capabilities", {}).values()
        )
    ):
        raise CapabilityDiscoveryError(
            "CAPABILITY_DISCOVERY_STATE_INVALID",
            f"能力发现状态字段无效: {path}",
        )
    return value


def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    payload = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _save_unlocked(kb: Path, value: dict[str, Any], now: datetime) -> None:
    _ensure_state_ignored(kb)
    value["updated_at"] = _iso(now)
    _atomic_write(state_path(kb), value)


def _capability_state(state: dict[str, Any], capability_id: str) -> dict[str, Any]:
    capabilities = state.setdefault("capabilities", {})
    value = capabilities.setdefault(
        capability_id,
        {
            "uses": 0,
            "last_used_at": None,
            "impressions": 0,
            "last_shown_at": None,
            "disposition": "active",
            "snoozed_until": None,
        },
    )
    return value


def _count_markdown(path: Path, *, limit: int | None = None) -> int:
    if not path.is_dir():
        return 0
    count = 0
    for item in path.rglob("*.md"):
        if not item.is_file():
            continue
        count += 1
        if limit is not None and count >= limit:
            break
    return count


def _observations(kb: Path, *, bounded: bool = False) -> dict[str, Any]:
    knowledge = kb / "knowledge"
    knowledge_limit = 20 if bounded else None
    project_limit = 3 if bounded else None
    report_limit = 2 if bounded else None
    report_state = kb / "state" / "report_automation.json"
    report_enabled = False
    if report_state.is_file():
        try:
            value = json.loads(report_state.read_text(encoding="utf-8"))
            report_enabled = bool(
                value.get("decision") == "configured"
                or value.get("daily", {}).get("enabled")
                or value.get("weekly", {}).get("enabled")
            )
        except (OSError, json.JSONDecodeError, AttributeError):
            report_enabled = False
    return {
        "knowledge_nodes": _count_markdown(knowledge, limit=knowledge_limit),
        "projects": _count_markdown(knowledge / "projects", limit=project_limit),
        "sources": sum(1 for item in (kb / "sources").glob("*.json") if item.is_file())
        if (kb / "sources").is_dir()
        else 0,
        "reports": min(
            report_limit or sys.maxsize,
            _count_markdown(kb / "reports" / "daily", limit=report_limit)
            + _count_markdown(kb / "reports" / "weekly", limit=report_limit),
        ),
        "report_automation_enabled": report_enabled,
    }


def _is_unused(state: Mapping[str, Any], capability_id: str) -> bool:
    events = state.get("events", {})
    capabilities = state.get("capabilities", {})
    capability = capabilities.get(capability_id, {}) if isinstance(capabilities, Mapping) else {}
    return int(events.get(capability_id, 0)) == 0 and int(capability.get("uses", 0)) == 0


def _available_for_tip(
    state: Mapping[str, Any], capability_id: str, now: datetime
) -> bool:
    capabilities = state.get("capabilities", {})
    value = capabilities.get(capability_id, {}) if isinstance(capabilities, Mapping) else {}
    if value.get("disposition", "active") == "dismissed":
        return False
    if int(value.get("impressions", 0)) >= MAX_CAPABILITY_IMPRESSIONS:
        return False
    snoozed_until = _parse_time(value.get("snoozed_until"))
    return snoozed_until is None or snoozed_until <= now


def _cadence_ready(state: Mapping[str, Any], now: datetime) -> bool:
    last_tip = _parse_time(state.get("last_tip_at"))
    if last_tip is None:
        return True
    if now - last_tip >= TIP_COOLDOWN:
        return True
    return int(state.get("successful_uses_since_tip", 0)) >= TIP_SUCCESS_INTERVAL


def _thresholds_match(actual: Mapping[str, Any], expected: object) -> bool:
    if not isinstance(expected, Mapping):
        return True
    for key, minimum in expected.items():
        if int(actual.get(key, 0)) < int(minimum):
            return False
    return True


def _rule_matches(
    rule: Mapping[str, Any],
    *,
    state: Mapping[str, Any],
    facts: Mapping[str, Any],
    now: datetime,
    after: str,
    signals: set[str],
    background: bool,
) -> bool:
    if bool(rule.get("background")) != background:
        return False
    after_any = set(rule.get("after_any", []))
    if after_any and after not in after_any:
        return False
    signal_any = set(rule.get("signal_any", []))
    if signal_any and not signal_any.intersection(signals):
        return False
    events = state.get("events", {})
    if not isinstance(events, Mapping) or not _thresholds_match(
        events, rule.get("minimum_events")
    ):
        return False
    if not _thresholds_match(facts, rule.get("minimum_facts")):
        return False
    equal = rule.get("facts_equal", {})
    if isinstance(equal, Mapping) and any(facts.get(key) != value for key, value in equal.items()):
        return False
    if any(not _is_unused(state, item) for item in rule.get("requires_unused", [])):
        return False
    capability_id = str(rule["capability_id"])
    if not _available_for_tip(state, capability_id, now):
        return False
    capabilities = state.get("capabilities", {})
    capability = capabilities.get(capability_id, {}) if isinstance(capabilities, Mapping) else {}
    if int(capability.get("impressions", 0)) >= int(
        rule.get("max_impressions", MAX_CAPABILITY_IMPRESSIONS)
    ):
        return False
    return bool(rule.get("bypass_cooldown")) or _cadence_ready(state, now)


def _select_suggestion(
    catalog: Mapping[str, Any],
    *,
    state: Mapping[str, Any],
    facts: Mapping[str, Any],
    now: datetime,
    after: str = "",
    signals: set[str] | None = None,
    background: bool = False,
) -> dict[str, Any] | None:
    capabilities = {item["id"]: item for item in catalog["capabilities"]}
    rules = sorted(
        catalog["recommendations"],
        key=lambda item: int(item.get("priority", 0)),
        reverse=True,
    )
    for rule in rules:
        if not _rule_matches(
            rule,
            state=state,
            facts=facts,
            now=now,
            after=after,
            signals=signals or set(),
            background=background,
        ):
            continue
        capability = capabilities[rule["capability_id"]]
        return {
            "recommendation_id": rule["id"],
            "capability_id": capability["id"],
            "title": capability["title"],
            "message": rule["message"],
            "example": capability["example"],
            "setup": capability["setup"],
        }
    return None


def _record_shown(
    state: dict[str, Any], capability_id: str, now: datetime
) -> None:
    value = _capability_state(state, capability_id)
    value["impressions"] = int(value.get("impressions", 0)) + 1
    value["last_shown_at"] = _iso(now)
    value["snoozed_until"] = None
    state["last_tip_at"] = _iso(now)
    state["successful_uses_since_tip"] = 0


def status(
    kb: Path, *, now: datetime | None = None, catalog_path: Path | None = None
) -> dict[str, Any]:
    current = now or _now()
    catalog = load_catalog(catalog_path)
    facts = _observations(kb)
    with _state_lock(kb):
        state = _load_unlocked(kb, current)
    result = []
    for capability in catalog["capabilities"]:
        capability_id = capability["id"]
        value = state.get("capabilities", {}).get(capability_id, {})
        disposition = value.get("disposition", "active")
        if disposition == "dismissed":
            progress = "dismissed"
        elif not _is_unused(state, capability_id):
            progress = "used"
        else:
            progress = "available"
        result.append({**capability, "progress": progress})
    return {
        "schema_version": STATUS_SCHEMA,
        "tips_enabled": bool(state.get("tips_enabled", True)),
        "capabilities": result,
        "observations": facts,
    }


def recommend(
    kb: Path,
    *,
    after: str,
    signals: Sequence[str] = (),
    now: datetime | None = None,
    catalog_path: Path | None = None,
) -> dict[str, Any]:
    current = now or _now()
    catalog = load_catalog(catalog_path)
    known = {item["id"] for item in catalog["capabilities"]}
    if after not in known:
        raise CapabilityDiscoveryError(
            "CAPABILITY_UNKNOWN", f"未知能力: {after}"
        )
    normalized_signals = set(signals)
    unknown_signals = normalized_signals - VALID_SIGNALS
    if unknown_signals:
        raise CapabilityDiscoveryError(
            "CAPABILITY_SIGNAL_UNKNOWN",
            "未知能力发现信号: " + ", ".join(sorted(unknown_signals)),
        )
    facts = _observations(kb, bounded=True)
    with _state_lock(kb):
        state = _load_unlocked(kb, current)
        events = state.setdefault("events", {})
        events[after] = int(events.get(after, 0)) + 1
        used = _capability_state(state, after)
        used["uses"] = int(used.get("uses", 0)) + 1
        used["last_used_at"] = _iso(current)
        state["successful_uses"] = int(state.get("successful_uses", 0)) + 1
        state["successful_uses_since_tip"] = int(
            state.get("successful_uses_since_tip", 0)
        ) + 1
        suggestion = None
        if state.get("tips_enabled", True):
            suggestion = _select_suggestion(
                catalog,
                state=state,
                facts=facts,
                now=current,
                after=after,
                signals=normalized_signals,
            )
        if suggestion:
            _record_shown(state, suggestion["capability_id"], current)
        _save_unlocked(kb, state, current)
    return {
        "schema_version": RECOMMENDATION_SCHEMA,
        "recorded_success": after,
        "suggestion": suggestion,
    }


def peek_background_recommendation(
    kb: Path, *, now: datetime | None = None, catalog_path: Path | None = None
) -> dict[str, Any] | None:
    current = now or _now()
    catalog = load_catalog(catalog_path)
    facts = _observations(kb, bounded=True)
    with _state_lock(kb):
        state = _load_unlocked(kb, current)
    if not state.get("tips_enabled", True):
        return None
    return _select_suggestion(
        catalog,
        state=state,
        facts=facts,
        now=current,
        background=True,
    )


def feedback(
    kb: Path,
    *,
    action: str,
    capability_id: str = "",
    days: int = 30,
    now: datetime | None = None,
    catalog_path: Path | None = None,
) -> dict[str, Any]:
    current = now or _now()
    known = set(capability_ids(catalog_path))
    per_capability = {"shown", "dismiss", "snooze", "used"}
    if action in per_capability and capability_id not in known:
        raise CapabilityDiscoveryError(
            "CAPABILITY_UNKNOWN", "该反馈动作需要有效的 capability id。"
        )
    if action not in {*per_capability, "enable", "disable"}:
        raise CapabilityDiscoveryError(
            "CAPABILITY_FEEDBACK_INVALID", f"未知反馈动作: {action}"
        )
    if days <= 0:
        raise CapabilityDiscoveryError(
            "CAPABILITY_FEEDBACK_INVALID", "snooze days 必须大于 0。"
        )
    with _state_lock(kb):
        state = _load_unlocked(kb, current)
        if action == "enable":
            state["tips_enabled"] = True
        elif action == "disable":
            state["tips_enabled"] = False
        else:
            value = _capability_state(state, capability_id)
            if action == "shown":
                _record_shown(state, capability_id, current)
            elif action == "dismiss":
                value["disposition"] = "dismissed"
                value["snoozed_until"] = None
            elif action == "snooze":
                value["snoozed_until"] = _iso(current + timedelta(days=days))
            elif action == "used":
                value["uses"] = int(value.get("uses", 0)) + 1
                value["last_used_at"] = _iso(current)
        _save_unlocked(kb, state, current)
    return {
        "schema_version": STATE_SCHEMA,
        "action": action,
        "capability_id": capability_id or None,
        "tips_enabled": bool(state.get("tips_enabled", True)),
    }
