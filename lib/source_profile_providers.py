"""Provider validators for v2 Source Profiles.

The lifecycle, persistence, revision, and schema dispatch stay in
``source_profiles.py``.  Provider-specific selector and capture-policy rules
live here so adding a source does not keep growing the lifecycle module.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from source_profile_contract import SourceProfileError


def _reject_unknown(
    value: Mapping[str, Any],
    allowed: set[str],
    field: str,
) -> None:
    unknown = sorted(str(key) for key in set(value) - allowed)
    if unknown:
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            f"source profile 的 {field} 含未知字段: {', '.join(unknown)}",
        )


def _required_text(value: Any, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            f"source profile 的 {field} 不能为空",
        )
    return normalized


def _positive_int(value: Any, field: str) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            f"source profile 的 {field} 必须是正整数",
        ) from exc
    if parsed <= 0:
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            f"source profile 的 {field} 必须是正整数",
        )
    return parsed


def _nonnegative_int(value: Any, field: str) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            f"source profile 的 {field} 必须是非负整数",
        ) from exc
    if parsed < 0:
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            f"source profile 的 {field} 必须是非负整数",
        )
    return parsed


def _stable_string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list):
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            f"source profile 的 {field} 必须是非空数组",
        )
    normalized = sorted(
        {
            item.strip()
            for item in value
            if isinstance(item, str) and item.strip()
        }
    )
    if not normalized or len(normalized) != len(value):
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            f"source profile 的 {field} 必须是非空且不重复的字符串数组",
        )
    return normalized


def _optional_iso8601(value: Any, field: str) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str):
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            f"source profile 的 {field} 必须是带时区的 ISO8601 字符串或为空",
        )
    text = value.strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(
            text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
        )
    except ValueError as exc:
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            f"source profile 的 {field} 必须是带时区的 ISO8601 字符串或为空",
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            f"source profile 的 {field} 必须包含时区",
        )
    return parsed.astimezone(timezone.utc).isoformat()


def validate_feishu_base_v2(
    *,
    source_uid: str,
    selector: Mapping[str, Any],
    capture_policy: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    _reject_unknown(selector, {"app_token", "table_id", "view_id"}, "selector")
    app_token = _required_text(selector.get("app_token"), "selector.app_token")
    table_id = _required_text(selector.get("table_id"), "selector.table_id")
    view_id = _required_text(selector.get("view_id"), "selector.view_id")
    expected_uid = f"feishu_base:{app_token}:{table_id}:{view_id}"
    if source_uid != expected_uid:
        raise SourceProfileError(
            "SOURCE_PROFILE_IDENTITY_MISMATCH",
            "source profile 的 source_uid 与 Base selector 不一致",
        )

    _reject_unknown(
        capture_policy,
        {"fields", "page_size", "max_records"},
        "capture_policy",
    )
    fields = _stable_string_list(
        capture_policy.get("fields"),
        "capture_policy.fields",
    )
    page_size = _positive_int(
        capture_policy.get("page_size"),
        "capture_policy.page_size",
    )
    if page_size > 500:
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            "source profile.capture_policy.page_size 不得超过 500",
        )
    max_records = _positive_int(
        capture_policy.get("max_records"),
        "capture_policy.max_records",
    )
    return (
        {"app_token": app_token, "table_id": table_id, "view_id": view_id},
        {
            "fields": fields,
            "page_size": page_size,
            "max_records": max_records,
        },
    )


def validate_feishu_chat_v2(
    *,
    source_uid: str,
    selector: Mapping[str, Any],
    capture_policy: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    _reject_unknown(selector, {"chat_id"}, "selector")
    chat_id = _required_text(selector.get("chat_id"), "selector.chat_id")
    if source_uid != chat_id:
        raise SourceProfileError(
            "SOURCE_PROFILE_IDENTITY_MISMATCH",
            "source profile 的 source_uid 与 selector.chat_id 不一致",
        )
    _reject_unknown(
        capture_policy,
        {"start", "end", "since_last", "page_size", "overlap_seconds"},
        "capture_policy",
    )
    since_last = capture_policy.get("since_last")
    if not isinstance(since_last, bool):
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            "source profile.capture_policy.since_last 必须是布尔值",
        )
    start = _optional_iso8601(
        capture_policy.get("start"),
        "capture_policy.start",
    )
    end = _optional_iso8601(
        capture_policy.get("end"),
        "capture_policy.end",
    )
    page_size = _positive_int(
        capture_policy.get("page_size"),
        "capture_policy.page_size",
    )
    if page_size > 50:
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            "群聊 source profile.capture_policy.page_size 不得超过 50",
        )
    overlap_seconds = _nonnegative_int(
        capture_policy.get("overlap_seconds", 0),
        "capture_policy.overlap_seconds",
    )
    if since_last and start:
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            "since_last=true 时不得同时保存 capture_policy.start",
        )
    if not since_last:
        if not start or not end:
            raise SourceProfileError(
                "SOURCE_PROFILE_INVALID",
                "since_last=false 时必须同时保存 capture_policy.start/end",
            )
        if overlap_seconds:
            raise SourceProfileError(
                "SOURCE_PROFILE_INVALID",
                "overlap_seconds 只适用于 since_last=true 的增量摄取",
            )
    if start and end and datetime.fromisoformat(start) >= datetime.fromisoformat(end):
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            "capture_policy.start 必须早于 end",
        )
    return (
        {"chat_id": chat_id},
        {
            "start": start,
            "end": end,
            "since_last": since_last,
            "page_size": page_size,
            "overlap_seconds": overlap_seconds,
        },
    )


def validate_feishu_wiki_v2(
    *,
    source_uid: str,
    selector: Mapping[str, Any],
    capture_policy: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate a monitored Wiki subtree, not a captured document source."""

    _reject_unknown(
        selector,
        {"space_id", "root_node_token"},
        "selector",
    )
    space_id = _required_text(selector.get("space_id"), "selector.space_id")
    root_node_token = _required_text(
        selector.get("root_node_token"),
        "selector.root_node_token",
    )
    expected_uid = f"feishu_wiki:{space_id}:{root_node_token}"
    if source_uid != expected_uid:
        raise SourceProfileError(
            "SOURCE_PROFILE_IDENTITY_MISMATCH",
            "source profile 的 source_uid 与 Wiki selector 不一致",
        )

    _reject_unknown(
        capture_policy,
        {
            "max_depth",
            "max_nodes",
            "include_types",
            "change_detection",
        },
        "capture_policy",
    )
    max_depth_value = capture_policy.get("max_depth")
    max_depth = (
        None
        if max_depth_value in (None, "")
        else _nonnegative_int(max_depth_value, "capture_policy.max_depth")
    )
    max_nodes = _positive_int(
        capture_policy.get("max_nodes"),
        "capture_policy.max_nodes",
    )
    if max_nodes > 100_000:
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            "Wiki source profile.capture_policy.max_nodes 不得超过 100000",
        )
    include_types = _stable_string_list(
        capture_policy.get("include_types"),
        "capture_policy.include_types",
    )
    unsupported = sorted(set(include_types) - {"doc", "docx"})
    if unsupported:
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            "Wiki include_types 暂只支持 doc、docx",
        )
    change_detection = _required_text(
        capture_policy.get("change_detection"),
        "capture_policy.change_detection",
    )
    if change_detection not in {
        "structure_only",
        "new_pages",
        "new_and_updated",
    }:
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            "Wiki change_detection 必须是 structure_only、new_pages 或 "
            "new_and_updated",
        )
    return (
        {
            "space_id": space_id,
            "root_node_token": root_node_token,
        },
        {
            "max_depth": max_depth,
            "max_nodes": max_nodes,
            "include_types": include_types,
            "change_detection": change_detection,
        },
    )
