"""Intent-scoped projections of the user's persistent context.md."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


CONTEXT_VIEW_SCHEMA = "byteworker-context-view/v1"
SOFT_BUDGET = 12_000
HARD_BUDGET = 24_000
INTENT_SECTIONS = {
    "todo": ("我的身份", "交互与提醒偏好"),
    "search": ("我的职责范围", "我的当前重点", "背景信息"),
    "digest": (
        "我的身份",
        "我的职责范围",
        "我的当前重点",
        "主管方向",
        "当前约束",
    ),
    "update": ("我的职责范围", "我的当前重点", "当前约束"),
    "brief": ("我的身份", "我的当前重点", "主管方向"),
    "dashboard": (
        "我的职责范围",
        "我的当前重点",
        "当前约束",
        "交互与提醒偏好",
    ),
    "report": (
        "我的身份",
        "我的职责范围",
        "我的当前重点",
        "主管方向",
        "当前约束",
        "背景信息",
    ),
    "inbox": (
        "我的身份",
        "我的职责范围",
        "我的当前重点",
        "主管方向",
    ),
}


class ContextViewError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def parse_context(text: str) -> dict[str, str]:
    matches = list(re.finditer(r"(?m)^##[ \t]+(.+?)[ \t]*\r?$", text))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        heading = match.group(1).strip()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[heading] = text[match.end() : end].strip()
    return sections


def context_view(kb: Path, intent: str) -> dict[str, Any]:
    normalized_intent = intent.strip().lower()
    selected = INTENT_SECTIONS.get(normalized_intent)
    if selected is None:
        raise ContextViewError(
            "CONTEXT_INTENT_INVALID",
            "intent 必须是 " + ", ".join(sorted(INTENT_SECTIONS)),
        )
    path = kb.expanduser().resolve() / "context.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ContextViewError(
            "CONTEXT_READ_ERROR",
            f"无法读取 context.md: {exc}",
        ) from exc
    sections = parse_context(text)
    missing = [heading for heading in selected if heading not in sections]
    if missing:
        raise ContextViewError(
            "CONTEXT_SCHEMA_INVALID",
            "context.md 缺少固定章节: " + ", ".join(missing),
        )
    projection = {heading: sections[heading] for heading in selected}
    character_count = sum(len(value) for value in projection.values())
    if character_count > HARD_BUDGET:
        raise ContextViewError(
            "CONTEXT_BUDGET_EXCEEDED",
            f"{normalized_intent} context 投影为 {character_count} 字符，"
            f"超过硬上限 {HARD_BUDGET}；请先归档过期条目",
        )
    warnings = []
    if character_count > SOFT_BUDGET:
        warnings.append(
            f"context 投影为 {character_count} 字符，超过建议上限 {SOFT_BUDGET}"
        )
    return {
        "schema_version": CONTEXT_VIEW_SCHEMA,
        "intent": normalized_intent,
        "sections": projection,
        "character_count": character_count,
        "soft_budget": SOFT_BUDGET,
        "hard_budget": HARD_BUDGET,
        "warnings": warnings,
    }
