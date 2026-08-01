"""Validation for bounded Agent semantic decisions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


IM_SCHEMA = "byteworker-im-semantic/v1"
IM_REASON_CODES = {
    "explicit_decision",
    "project_status_change",
    "key_risk",
    "cross_team_alignment",
    "user_assigned_action",
    "important_information",
}
KB_PROMOTION_REASONS = {
    "explicit_decision",
    "dated_status_change",
    "time_bounded_event",
    "cross_record_theme",
    "long_running_project",
}
KB_DIGEST_REASONS = {
    "explicit_decision",
    "project_status_change",
    "key_risk",
    "cross_team_alignment",
}


class SemanticPolicyError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _bounded_strings(value: Any, field: str, *, limit: int = 100) -> list[str]:
    if not isinstance(value, list) or len(value) > limit:
        raise SemanticPolicyError(
            "IM_SEMANTIC_INVALID",
            f"{field} 必须是最多 {limit} 项的数组",
        )
    result = []
    for item in value:
        text = str(item).strip()
        if not text or len(text) > 1000:
            raise SemanticPolicyError(
                "IM_SEMANTIC_INVALID",
                f"{field} 含空值或超长文本",
            )
        result.append(text)
    return result


def validate_im_semantic(value: Mapping[str, Any]) -> dict[str, Any]:
    if value.get("schema_version") != IM_SCHEMA:
        raise SemanticPolicyError(
            "IM_SEMANTIC_INVALID",
            f"schema_version 必须是 {IM_SCHEMA}",
        )
    raw_threads = value.get("threads")
    if not isinstance(raw_threads, list) or len(raw_threads) > 100:
        raise SemanticPolicyError(
            "IM_SEMANTIC_INVALID",
            "threads 必须是最多 100 项的数组",
        )
    threads = []
    for index, raw in enumerate(raw_threads):
        if not isinstance(raw, Mapping):
            raise SemanticPolicyError(
                "IM_SEMANTIC_INVALID",
                f"threads[{index}] 必须是对象",
            )
        importance = raw.get("importance")
        relevance = raw.get("relevance_to_user")
        if (
            isinstance(importance, bool)
            or not isinstance(importance, int)
            or not 0 <= importance <= 4
            or isinstance(relevance, bool)
            or not isinstance(relevance, int)
            or not 0 <= relevance <= 4
        ):
            raise SemanticPolicyError(
                "IM_SEMANTIC_SCORE_INVALID",
                f"threads[{index}] importance/relevance 必须是 0..4 整数",
            )
        reasons = _bounded_strings(raw.get("reason_codes"), "reason_codes", limit=10)
        unknown = sorted(set(reasons) - IM_REASON_CODES)
        if unknown:
            raise SemanticPolicyError(
                "IM_SEMANTIC_REASON_INVALID",
                "未知 reason_code: " + ", ".join(unknown),
            )
        include = raw.get("should_include_report")
        digest = raw.get("should_digest_kb")
        expected_include = importance >= 3 and relevance >= 2
        expected_digest = expected_include and bool(set(reasons) & KB_DIGEST_REASONS)
        if include is not expected_include:
            raise SemanticPolicyError(
                "IM_SEMANTIC_THRESHOLD_MISMATCH",
                f"threads[{index}].should_include_report 与固定阈值不一致",
            )
        if digest is not expected_digest:
            raise SemanticPolicyError(
                "IM_SEMANTIC_THRESHOLD_MISMATCH",
                f"threads[{index}].should_digest_kb 与固定阈值/reason 不一致",
            )
        sources = raw.get("sources")
        if not isinstance(sources, list) or not sources:
            raise SemanticPolicyError(
                "IM_SEMANTIC_EVIDENCE_MISSING",
                f"threads[{index}] 缺少 sources",
            )
        normalized_sources = []
        for source in sources:
            if not isinstance(source, Mapping):
                raise SemanticPolicyError(
                    "IM_SEMANTIC_EVIDENCE_MISSING",
                    f"threads[{index}].sources 必须是对象数组",
                )
            message_ids = _bounded_strings(
                source.get("message_ids"),
                "message_ids",
                limit=200,
            )
            chat_id = str(source.get("chat_id", "")).strip()
            window = str(source.get("window", "")).strip()
            if not chat_id or not window or not message_ids:
                raise SemanticPolicyError(
                    "IM_SEMANTIC_EVIDENCE_MISSING",
                    f"threads[{index}] source 缺 chat_id/window/message_ids",
                )
            normalized_sources.append(
                {
                    "chat_id": chat_id,
                    "window": window,
                    "message_ids": message_ids,
                }
            )
        threads.append(
            {
                "importance": importance,
                "relevance_to_user": relevance,
                "reason_codes": reasons,
                "should_include_report": include,
                "should_digest_kb": digest,
                "title": str(raw.get("title", "")).strip()[:500],
                "summary": str(raw.get("summary", "")).strip()[:4000],
                "facts": _bounded_strings(raw.get("facts"), "facts"),
                "actions": _bounded_strings(raw.get("actions"), "actions"),
                "risks": _bounded_strings(raw.get("risks"), "risks"),
                "sources": normalized_sources,
            }
        )
    return {"schema_version": IM_SCHEMA, "threads": threads}


def validate_im_semantic_file(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SemanticPolicyError(
            "IM_SEMANTIC_INVALID",
            f"无法读取 IM semantic JSON: {exc}",
        ) from exc
    if not isinstance(value, Mapping):
        raise SemanticPolicyError("IM_SEMANTIC_INVALID", "顶层必须是对象")
    return validate_im_semantic(value)
