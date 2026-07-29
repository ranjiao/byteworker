"""Legacy structured-snapshot record projection for query.

New collection adapters should persist canonical record projections.  Until
all historical raw files carry them, provider-shaped snapshots are normalized
here so query core does not grow source-type branches.
"""

from __future__ import annotations

import unicodedata
from typing import Any, Mapping, Sequence


STRUCTURED_SOURCE_TYPES = frozenset({"meego", "feishu_base", "aeolus"})
TITLE_KEYS = (
    "work_item_name",
    "workItemName",
    "record_name",
    "recordName",
    "title",
    "name",
    "summary",
    "subject",
)
BASE_TITLE_FIELD_HINTS = (
    "title",
    "name",
    "summary",
    "subject",
    "标题",
    "名称",
    "主题",
    "需求",
    "事项",
)


def _mapping_child(
    value: Any,
    keys: Sequence[str],
) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    for key in keys:
        child = value.get(key)
        if isinstance(child, Mapping):
            return child
    return None


def _record_id(record: Any, source_type: str) -> str:
    if not isinstance(record, Mapping):
        return ""
    keys = (
        ("work_item_id", "workItemId", "id")
        if source_type == "meego"
        else ("record_id", "recordId", "id")
    )
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return str(value)
    wrappers = (
        (
            "work_item",
            "workItem",
            "work_item_info",
            "workItemInfo",
            "work_item_attribute",
            "workItemAttribute",
        )
        if source_type == "meego"
        else ("record",)
    )
    child = _mapping_child(record, wrappers)
    if child:
        for key in keys:
            value = child.get(key)
            if value not in (None, ""):
                return str(value)
    return ""


def _first_text_by_key(
    value: Any,
    keys: Sequence[str],
) -> tuple[str, str]:
    if not isinstance(value, Mapping):
        return "", ""
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, (str, int, float)) and str(candidate).strip():
            return str(candidate).strip(), key
    for wrapper in (
        "work_item",
        "workItem",
        "work_item_info",
        "workItemInfo",
        "work_item_attribute",
        "workItemAttribute",
        "record",
    ):
        child = value.get(wrapper)
        if not isinstance(child, Mapping):
            continue
        text, field = _first_text_by_key(child, keys)
        if text:
            return text, f"{wrapper}.{field}"
    return "", ""


def _flatten_text(value: Any, *, limit: int = 12) -> list[str]:
    result: list[str] = []

    def visit(current: Any) -> None:
        if len(result) >= limit:
            return
        if isinstance(current, str):
            text = current.strip()
            if text and not text.startswith(("http://", "https://")):
                result.append(text)
            return
        if isinstance(current, (int, float)) and not isinstance(current, bool):
            result.append(str(current))
            return
        if isinstance(current, Mapping):
            for child in current.values():
                visit(child)
            return
        if isinstance(current, list):
            for child in current:
                visit(child)

    visit(value)
    return list(dict.fromkeys(result))


def _normalize(value: str) -> tuple[str, str]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    spaced = "".join(
        character
        if unicodedata.category(character)[0] in {"L", "N"}
        else " "
        for character in normalized
    )
    words = " ".join(spaced.split())
    return words, words.replace(" ", "")


def _base_title_candidates(
    record: Mapping[str, Any],
) -> list[tuple[str, str, float]]:
    container = record.get("fields")
    if not isinstance(container, Mapping):
        child = _mapping_child(record, ("record",))
        container = child.get("fields") if child else None
    if not isinstance(container, Mapping):
        return []
    preferred: list[tuple[str, str, float]] = []
    fallback: list[tuple[str, str, float]] = []
    for field, value in container.items():
        texts = _flatten_text(value)
        if not texts:
            continue
        combined = " ".join(texts)
        field_words, field_compact = _normalize(str(field))
        hinted = any(
            hint in field_words or _normalize(hint)[1] in field_compact
            for hint in BASE_TITLE_FIELD_HINTS
        )
        candidate = (f"fields.{field}", combined, 1.0 if hinted else 0.82)
        (preferred if hinted else fallback).append(candidate)
    return preferred or fallback


def _title_candidates(
    record: Any,
    source_type: str,
) -> list[tuple[str, str, float]]:
    if not isinstance(record, Mapping):
        return []
    if source_type == "feishu_base":
        return _base_title_candidates(record)
    title, field = _first_text_by_key(record, TITLE_KEYS)
    return [(field, title, 1.0)] if title else []


def project_legacy_record(
    record: Any,
    source_type: str,
) -> dict[str, Any]:
    """Return canonical query coordinates without changing the raw record."""

    record_id = _record_id(record, source_type)
    if source_type == "meego":
        anchor_id = f"workitem:{record_id}"
    elif source_type == "aeolus":
        anchor_id = f"aeolus:{record_id}"
    else:
        anchor_id = f"record:{record_id}"
    return {
        "record_id": record_id,
        "title_candidates": _title_candidates(record, source_type),
        "anchor_id": anchor_id,
    }
