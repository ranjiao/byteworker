"""Compatibility boundary between source adapters and digest transaction v1.

Provider-specific requirements belong here (or in a source adapter), not in
the transaction engine.  ``digest_txn`` passes typed component payload objects
without importing them here, which keeps this module free of circular imports.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence


class SourceTransactionError(ValueError):
    pass


SUPPORTED_TRANSACTION_SOURCE_TYPES = frozenset(
    {
        "feishu_doc",
        "feishu_minutes",
        "feishu_meeting",
        "feishu_chat",
        "feishu_base",
        "meego",
        "aeolus",
        "web",
        "local_md",
    }
)


def _list_value(value: Any) -> list[str]:
    if isinstance(value, list):
        return [
            str(item).strip().strip('"').strip("'")
            for item in value
            if str(item).strip()
        ]
    if value:
        return [str(value).strip()]
    return []


def _require_text(source: Mapping[str, Any], source_type: str, fields: Sequence[str]) -> None:
    for field in fields:
        if not str(source.get(field, "")).strip():
            raise SourceTransactionError(
                f"{source_type} 的 source.{field} 不能为空"
            )


def validate_transaction_source(
    source: Mapping[str, Any],
    components: Sequence[Any],
) -> None:
    """Validate the legacy transaction representation emitted by adapters."""

    source_type = str(source.get("type", "")).strip()
    body_components = [item for item in components if item.kind == "body"]
    comment_components = [item for item in components if item.kind == "comments"]
    whiteboards = [item for item in components if item.kind == "whiteboard"]

    if len(comment_components) > 1:
        raise SourceTransactionError(
            "一个 source 最多只能包含一个 kind=comments component"
        )

    validators = {
        "feishu_chat": lambda: _require_text(
            source, source_type, ("uid", "title", "source_window")
        ),
        "feishu_minutes": lambda: _require_text(
            source, source_type, ("url", "title")
        ),
        "feishu_meeting": lambda: _require_text(
            source, source_type, ("url", "title")
        ),
        "web": lambda: _require_text(source, source_type, ("url", "title")),
        "meego": lambda: _validate_meego(source),
        "feishu_base": lambda: _validate_feishu_base(source),
        "aeolus": lambda: _validate_aeolus(source),
    }
    if source_type == "feishu_doc":
        _validate_feishu_doc(
            source,
            components,
            body_components,
            comment_components,
            whiteboards,
        )
    elif source_type in validators:
        validators[source_type]()


def _validate_feishu_doc(
    source: Mapping[str, Any],
    components: Sequence[Any],
    body_components: Sequence[Any],
    comment_components: Sequence[Any],
    whiteboards: Sequence[Any],
) -> None:
    if len(body_components) != 1:
        raise SourceTransactionError(
            "feishu_doc 必须且只能包含一个 kind=body component"
        )
    if components[0].kind != "body":
        raise SourceTransactionError(
            "feishu_doc 的第一个 component 必须是 kind=body"
        )
    _require_text(source, "feishu_doc", ("uid", "revision", "url", "title"))
    comments_status = str(source.get("comments_status", "")).strip()
    if comments_status not in {"complete", "partial", "unavailable"}:
        raise SourceTransactionError(
            "feishu_doc 的 source.comments_status 必须是 "
            "complete/partial/unavailable"
        )
    if comments_status in {"complete", "partial"} and len(comment_components) != 1:
        raise SourceTransactionError(
            f"comments_status={comments_status} 时必须包含 kind=comments component"
        )
    if comments_status == "unavailable" and comment_components:
        raise SourceTransactionError(
            "comments_status=unavailable 时不得提供伪造的 comments component"
        )
    if comment_components and source.get("comment_count") not in (None, ""):
        try:
            comments_value = json.loads(comment_components[0].data.decode("utf-8"))
            actual_count = (
                len(comments_value) if isinstance(comments_value, list) else None
            )
            expected_count = int(source["comment_count"])
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise SourceTransactionError(
                "source.comment_count 或评论组件格式非法"
            ) from exc
        if actual_count is None or actual_count != expected_count:
            raise SourceTransactionError(
                f"comment_count 不一致: expected={expected_count}, "
                f"actual={actual_count}"
            )
    if whiteboards:
        whiteboards_status = str(source.get("whiteboards_status", "")).strip()
        if whiteboards_status not in {"complete", "partial"}:
            raise SourceTransactionError(
                "包含 whiteboard component 时必须声明 "
                "source.whiteboards_status=complete/partial"
            )


def _validate_meego(source: Mapping[str, Any]) -> None:
    _require_text(source, "meego", ("url", "title", "project_key", "view_id"))
    if not _list_value(source.get("fields")):
        raise SourceTransactionError("meego 的 source.fields 不能为空")


def _validate_feishu_base(source: Mapping[str, Any]) -> None:
    _require_text(
        source,
        "feishu_base",
        ("url", "title", "base_token", "table_id", "view_id"),
    )
    if not _list_value(source.get("fields")):
        raise SourceTransactionError("feishu_base 的 source.fields 不能为空")


def _validate_aeolus(source: Mapping[str, Any]) -> None:
    _require_text(
        source,
        "aeolus",
        (
            "url",
            "title",
            "profile_path",
            "profile_revision",
            "region",
            "app_id",
            "dashboard_id",
            "sheet_id",
            "filter_mode",
        ),
    )
    profile_path = str(source["profile_path"])
    if (
        Path(profile_path).is_absolute()
        or not profile_path.startswith("sources/aeolus-")
        or not profile_path.endswith(".json")
    ):
        raise SourceTransactionError(
            "aeolus 的 source.profile_path 必须指向 KB sources/ 下的 profile"
        )
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(source["profile_revision"])):
        raise SourceTransactionError(
            "aeolus 的 source.profile_revision 必须是 canonical sha256"
        )
    if not _list_value(source.get("report_ids")):
        raise SourceTransactionError("aeolus 的 source.report_ids 不能为空")
    if source.get("filter_mode") not in {"dashboard", "explicit", "merge"}:
        raise SourceTransactionError(
            "aeolus 的 source.filter_mode 必须是 dashboard/explicit/merge"
        )
    where_filters = source.get("where_filters", [])
    if not isinstance(where_filters, list) or any(
        not isinstance(item, dict) for item in where_filters
    ):
        raise SourceTransactionError(
            "aeolus 的 source.where_filters 必须是对象数组"
        )
    if source.get("filter_mode") == "explicit" and not where_filters:
        raise SourceTransactionError(
            "aeolus explicit 模式的 source.where_filters 不能为空"
        )


def raw_source_fields(source: Mapping[str, Any]) -> list[tuple[str, Any]]:
    """Return stable raw frontmatter fields for all legacy source types."""

    source_type = source.get("type")
    return [
        ("source_type", source_type),
        ("source_uid", source.get("uid")),
        ("source_revision", source.get("revision")),
        ("source_profile_path", source.get("profile_path")),
        ("source_profile_revision", source.get("profile_revision")),
        (
            "source_chat_id",
            source.get("uid") if source_type == "feishu_chat" else None,
        ),
        (
            "source_chat_name",
            source.get("title") if source_type == "feishu_chat" else None,
        ),
        ("source_project_key", source.get("project_key")),
        ("source_base_token", source.get("base_token")),
        ("source_table_id", source.get("table_id")),
        ("source_view_id", source.get("view_id")),
        ("source_fields", source.get("fields")),
        ("source_region", source.get("region")),
        ("source_app_id", source.get("app_id")),
        ("source_dashboard_id", source.get("dashboard_id")),
        ("source_sheet_id", source.get("sheet_id")),
        ("source_report_ids", source.get("report_ids")),
        ("source_filter_mode", source.get("filter_mode")),
        (
            "source_where_filters",
            [
                json.dumps(
                    item,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                for item in source.get("where_filters", [])
            ],
        ),
        ("source_url", source.get("url")),
        (
            "source_title",
            source.get("title") if source_type != "feishu_chat" else None,
        ),
        ("digest_period", source.get("digest_period")),
        ("source_window", source.get("source_window")),
        ("comments_status", source.get("comments_status")),
        ("comment_count", source.get("comment_count")),
        ("comment_reply_count", source.get("comment_reply_count")),
        ("comments_latest_at", source.get("comments_latest_at")),
        ("whiteboards_status", source.get("whiteboards_status")),
    ]
