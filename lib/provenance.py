"""Deterministic provenance helpers for byteworker knowledge nodes.

Raw markdown remains immutable evidence.  A provenance sidecar records stable
source locators, while knowledge nodes carry human-readable, clickable E-links.
Only the standard library is used.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from frontmatter import parse_file, parse_frontmatter


PROVENANCE_SCHEMA = "byteworker-provenance/v1"
BACKFILL_SCHEMA = "byteworker-provenance-backfill/v1"
EVIDENCE_ID_RE = re.compile(r"^E[1-9][0-9]*$")
EVIDENCE_MARKER_RE = re.compile(r"\[(E[1-9][0-9]*)\]")
ANCHOR_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
ALLOWED_PRECISIONS = {"exact", "refetched", "source_only", "unresolved"}
ALLOWED_KINDS = {
    "source",
    "doc_block",
    "doc_comment",
    "doc_reply",
    "chat_message",
    "chat_thread",
    "minutes_segment",
    "meeting",
    "meego_workitem",
    "base_record",
    "web_section",
    "whiteboard_node",
    "local_span",
}
EVIDENCE_HEADING = "## 证据"
DOC_BLOCK_RE = re.compile(
    r"<(?:title|heading|h[1-6]|p|li|blockquote|table|pre|whiteboard)"
    r'\b[^>]*\bid="([A-Za-z0-9_-]+)"[^>]*>',
    re.IGNORECASE,
)
COMMENT_SNAPSHOT_RE = re.compile(
    r"(?ms)^## 文档评论原始快照\s*$.*?^```json\s*$\n(.*?)^```\s*$"
)


class ProvenanceError(RuntimeError):
    """A provenance schema or materialization error."""


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def list_value(value: Any) -> List[str]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def source_url_from_frontmatter(frontmatter: Mapping[str, Any]) -> str:
    for key in ("source_url", "recording_url"):
        value = str(frontmatter.get(key, "")).strip()
        if value:
            return value
    return ""


def source_title_from_frontmatter(frontmatter: Mapping[str, Any]) -> str:
    return str(
        frontmatter.get("source_title")
        or frontmatter.get("source_chat_name")
        or frontmatter.get("title")
        or frontmatter.get("raw_id")
        or "未命名来源"
    ).strip()


def scan_raws(kb: Path) -> Dict[str, Dict[str, Any]]:
    records: Dict[str, Dict[str, Any]] = {}
    for path in sorted((kb / "raw_data").glob("*.md")):
        frontmatter, body = parse_file(str(path))
        raw_id = str(frontmatter.get("raw_id", "")).strip()
        if not raw_id:
            continue
        if raw_id in records:
            raise ProvenanceError(f"知识库存在重复 raw_id: {raw_id}")
        records[raw_id] = {
            "path": path,
            "relative_path": str(path.relative_to(kb)),
            "frontmatter": frontmatter,
            "body": body,
            "file_sha256": sha256_bytes(path.read_bytes()),
        }
    return records


def provenance_path(kb: Path, raw_id: str) -> Path:
    if not raw_id.startswith("raw-") or "/" in raw_id or "\\" in raw_id:
        raise ProvenanceError(f"非法 raw_id: {raw_id}")
    return kb / "provenance" / f"{raw_id}.json"


def _valid_open_target(value: str) -> bool:
    if not value:
        return True
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return True
    if parsed.scheme == "" and not value.startswith("//"):
        return True
    return False


def normalize_anchor(
    raw_id: str,
    anchor: Mapping[str, Any],
    default_source_url: str = "",
) -> Dict[str, Any]:
    anchor_id = str(anchor.get("anchor_id", "")).strip()
    if not ANCHOR_ID_RE.fullmatch(anchor_id):
        raise ProvenanceError(f"非法 anchor_id: {anchor_id!r}")
    kind = str(anchor.get("kind", "")).strip()
    if kind not in ALLOWED_KINDS:
        raise ProvenanceError(f"anchor {anchor_id} 的 kind 非法: {kind!r}")
    precision = str(anchor.get("precision", "")).strip()
    if precision not in ALLOWED_PRECISIONS:
        raise ProvenanceError(
            f"anchor {anchor_id} 的 precision 必须是 "
            "exact/refetched/source_only/unresolved"
        )
    locator = anchor.get("locator", {})
    if not isinstance(locator, dict):
        raise ProvenanceError(f"anchor {anchor_id}.locator 必须是对象")
    open_url = str(anchor.get("open_url", "")).strip()
    fallback_url = str(anchor.get("fallback_url", "")).strip()
    if kind == "source" and not open_url:
        open_url = default_source_url
    if not _valid_open_target(open_url) or not _valid_open_target(fallback_url):
        raise ProvenanceError(f"anchor {anchor_id} 包含不安全的 URL")
    if precision in {"exact", "refetched"} and not locator:
        raise ProvenanceError(f"anchor {anchor_id} 精确定位必须包含 locator")
    if not open_url and not fallback_url and not locator:
        raise ProvenanceError(f"anchor {anchor_id} 没有 URL 或 locator")

    result: Dict[str, Any] = {
        "anchor_id": anchor_id,
        "raw_id": raw_id,
        "kind": kind,
        "precision": precision,
        "locator": locator,
    }
    for key, value in (
        ("label", str(anchor.get("label", "")).strip()),
        ("open_url", open_url),
        ("fallback_url", fallback_url),
        ("source_time", str(anchor.get("source_time", "")).strip()),
        ("author", str(anchor.get("author", "")).strip()),
    ):
        if value:
            result[key] = value
    quote = str(anchor.get("quote", "")).strip()
    if quote:
        result["quote"] = quote
        result["quote_sha256"] = sha256_bytes(quote.encode("utf-8"))
    return result


def source_anchor(raw_id: str, frontmatter: Mapping[str, Any]) -> Dict[str, Any]:
    locator: Dict[str, str] = {}
    for key in (
        "source_uid",
        "source_revision",
        "source_chat_id",
        "source_window",
        "digest_period",
    ):
        value = str(frontmatter.get(key, "")).strip()
        if value:
            locator[key] = value
    return normalize_anchor(
        raw_id,
        {
            "anchor_id": "source",
            "kind": "source",
            "precision": "source_only",
            "open_url": source_url_from_frontmatter(frontmatter),
            "label": source_title_from_frontmatter(frontmatter),
            "locator": locator,
            "source_time": (
                frontmatter.get("source_window")
                or frontmatter.get("digest_period")
                or frontmatter.get("event_time")
                or frontmatter.get("source_created_at")
                or ""
            ),
        },
    )


def _source_time(value: Any) -> str:
    """Normalize Lark epoch timestamps without guessing arbitrary strings."""
    if value in (None, ""):
        return ""
    if isinstance(value, str) and not value.strip().isdigit():
        return value.strip()
    try:
        timestamp = int(str(value).strip())
    except (TypeError, ValueError):
        return ""
    if timestamp > 10_000_000_000:
        timestamp /= 1000
    try:
        return datetime.fromtimestamp(
            timestamp, tz=ZoneInfo("Asia/Shanghai")
        ).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return ""


def _first_nested_value(value: Any, keys: Iterable[str]) -> str:
    if isinstance(value, Mapping):
        for key in keys:
            candidate = value.get(key)
            if candidate not in (None, ""):
                return str(candidate).strip()
        for child in value.values():
            candidate = _first_nested_value(child, keys)
            if candidate:
                return candidate
    elif isinstance(value, list):
        for child in value:
            candidate = _first_nested_value(child, keys)
            if candidate:
                return candidate
    return ""


def _relation_block_id(comment: Mapping[str, Any]) -> str:
    extra = comment.get("extra")
    block_id = _first_nested_value(extra, ("content_anchor_id",))
    if block_id:
        return block_id
    relation = comment.get("relation")
    if isinstance(relation, Mapping):
        encoded = relation.get("relation")
        if isinstance(encoded, str):
            try:
                relation = json.loads(encoded)
            except json.JSONDecodeError:
                pass
    return _first_nested_value(relation, ("blockID", "block_id", "blockId"))


def _rich_text(value: Any) -> str:
    texts: List[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            text_run = item.get("text_run")
            if isinstance(text_run, Mapping):
                text = str(text_run.get("text", "")).strip()
                if text:
                    texts.append(text)
            elif isinstance(item.get("text"), str):
                text = str(item["text"]).strip()
                if text:
                    texts.append(text)
            for key, child in item.items():
                if key not in {"text_run", "text"}:
                    visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return " ".join(texts)[:240]


def _comment_snapshot(body: str) -> List[Mapping[str, Any]]:
    match = COMMENT_SNAPSHOT_RE.search(body)
    if not match:
        return []
    try:
        value = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def extract_offline_anchors(
    raw_id: str,
    frontmatter: Mapping[str, Any],
    body: str,
) -> List[Dict[str, Any]]:
    anchors = [source_anchor(raw_id, frontmatter)]
    base_url = source_url_from_frontmatter(frontmatter)
    seen = {"source"}
    if str(frontmatter.get("source_type", "")) == "feishu_doc" and base_url:
        source_url = base_url.split("#", 1)[0]
        for block_id in DOC_BLOCK_RE.findall(body):
            anchor_id = f"doc:block:{block_id}"
            if anchor_id in seen:
                continue
            seen.add(anchor_id)
            anchors.append(
                normalize_anchor(
                    raw_id,
                    {
                        "anchor_id": anchor_id,
                        "kind": "doc_block",
                        "precision": "exact",
                        "open_url": source_url + "#" + block_id,
                        "fallback_url": base_url,
                        "locator": {"block_id": block_id},
                    },
                )
            )
        for comment in _comment_snapshot(body):
            comment_id = str(comment.get("comment_id", "")).strip()
            if not comment_id:
                continue
            block_id = _relation_block_id(comment)
            comment_anchor_id = f"doc:comment:{comment_id}"
            locator = {"comment_id": comment_id}
            if block_id:
                locator["block_id"] = block_id
            for key in ("parent_token", "parent_type"):
                value = str(comment.get(key, "")).strip()
                if value:
                    locator[key] = value
            anchors.append(
                normalize_anchor(
                    raw_id,
                    {
                        "anchor_id": comment_anchor_id,
                        "kind": "doc_comment",
                        "precision": "exact",
                        "open_url": (
                            source_url + "#" + block_id if block_id else source_url
                        ),
                        "fallback_url": base_url,
                        "locator": locator,
                        "source_time": _source_time(comment.get("create_time")),
                        "author": str(comment.get("user_id", "")).strip(),
                        "quote": str(comment.get("quote", "")).strip()[:240],
                    },
                )
            )
            seen.add(comment_anchor_id)
            reply_list = comment.get("reply_list")
            if not isinstance(reply_list, Mapping):
                continue
            replies = reply_list.get("replies")
            if not isinstance(replies, list):
                continue
            for reply in replies:
                if not isinstance(reply, Mapping):
                    continue
                reply_id = str(reply.get("reply_id", "")).strip()
                if not reply_id:
                    continue
                reply_anchor_id = (
                    f"doc:comment:{comment_id}:reply:{reply_id}"
                )
                reply_locator = {
                    "comment_id": comment_id,
                    "reply_id": reply_id,
                }
                if block_id:
                    reply_locator["block_id"] = block_id
                for key in ("parent_token", "parent_type"):
                    if key in locator:
                        reply_locator[key] = locator[key]
                anchors.append(
                    normalize_anchor(
                        raw_id,
                        {
                            "anchor_id": reply_anchor_id,
                            "kind": "doc_reply",
                            "precision": "exact",
                            "open_url": (
                                source_url + "#" + block_id
                                if block_id
                                else source_url
                            ),
                            "fallback_url": base_url,
                            "locator": reply_locator,
                            "source_time": _source_time(
                                reply.get("create_time")
                            ),
                            "author": str(reply.get("user_id", "")).strip(),
                            "quote": _rich_text(reply.get("content")),
                        },
                    )
                )
                seen.add(reply_anchor_id)
    return anchors


def build_provenance_document(
    raw_id: str,
    raw_relative_path: str,
    frontmatter: Mapping[str, Any],
    anchors: Sequence[Mapping[str, Any]],
    derived_hash: str,
    generated_at: str,
    enrichment: str,
) -> Dict[str, Any]:
    normalized = [
        normalize_anchor(
            raw_id,
            item,
            default_source_url=source_url_from_frontmatter(frontmatter),
        )
        for item in anchors
    ]
    if not any(item["anchor_id"] == "source" for item in normalized):
        normalized.insert(0, source_anchor(raw_id, frontmatter))
    ids = [item["anchor_id"] for item in normalized]
    if len(ids) != len(set(ids)):
        raise ProvenanceError(f"{raw_id} provenance 包含重复 anchor_id")
    return {
        "schema_version": PROVENANCE_SCHEMA,
        "raw_id": raw_id,
        "raw_path": raw_relative_path,
        "derived_from": {
            "content_hash": derived_hash,
            "source_revision": str(
                frontmatter.get("source_revision")
                or frontmatter.get("revision_id")
                or ""
            ),
        },
        "generated_at": generated_at,
        "enrichment": enrichment,
        "source": {
            "type": str(frontmatter.get("source_type", "")),
            "title": source_title_from_frontmatter(frontmatter),
            "url": source_url_from_frontmatter(frontmatter),
            "ingested": str(frontmatter.get("ingested", "")),
        },
        "anchors": normalized,
    }


def render_provenance(document: Mapping[str, Any]) -> str:
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def load_provenance(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProvenanceError(f"无法读取 provenance: {path}: {exc}") from exc
    if value.get("schema_version") != PROVENANCE_SCHEMA:
        raise ProvenanceError(f"不支持的 provenance schema: {path}")
    raw_id = str(value.get("raw_id", "")).strip()
    anchors = value.get("anchors")
    if not raw_id or not isinstance(anchors, list):
        raise ProvenanceError(f"provenance 缺少 raw_id/anchors: {path}")
    normalized = [normalize_anchor(raw_id, item) for item in anchors]
    result = dict(value)
    result["anchors"] = normalized
    return result


def scan_provenance(kb: Path) -> Dict[str, Dict[str, Any]]:
    records: Dict[str, Dict[str, Any]] = {}
    directory = kb / "provenance"
    if not directory.is_dir():
        return records
    for path in sorted(directory.glob("raw-*.json")):
        value = load_provenance(path)
        raw_id = value["raw_id"]
        if raw_id in records:
            raise ProvenanceError(f"重复 provenance raw_id: {raw_id}")
        records[raw_id] = value
    return records


def anchor_index(document: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {str(item["anchor_id"]): dict(item) for item in document.get("anchors", [])}


def _yaml_scalar(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9._:/|+@=-]+", value):
        return value
    return json.dumps(value, ensure_ascii=False)


def set_frontmatter_scalars(content: str, values: Mapping[str, str]) -> str:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ProvenanceError("节点缺少 frontmatter")
    end = next(
        (idx for idx in range(1, len(lines)) if lines[idx].strip() == "---"),
        None,
    )
    if end is None:
        raise ProvenanceError("节点 frontmatter 未闭合")
    for key, raw_value in values.items():
        value = str(raw_value).strip()
        if not value:
            continue
        replacement = f"{key}: {_yaml_scalar(value)}"
        found = False
        for idx in range(1, end):
            if re.match(rf"^{re.escape(key)}\s*:", lines[idx]):
                lines[idx] = replacement
                found = True
                break
        if not found:
            insert_at = next(
                (
                    idx
                    for idx in range(1, end)
                    if re.match(r"^sources\s*:", lines[idx])
                ),
                end,
            )
            lines.insert(insert_at, replacement)
            end += 1
    return "\n".join(lines) + ("\n" if content.endswith("\n") else "")


def strip_evidence_section(body: str) -> str:
    match = re.search(r"(?m)^## 证据\s*$", body)
    if not match:
        return body.rstrip()
    return body[: match.start()].rstrip()


def _cell(value: Any) -> str:
    text = str(value or "").replace("\n", " ").replace("|", "\\|").strip()
    return text or "未记录"


def _locator_summary(anchor: Mapping[str, Any]) -> str:
    kind_labels = {
        "source": "来源",
        "doc_block": "文档正文",
        "doc_comment": "文档评论",
        "doc_reply": "评论回复",
        "chat_message": "聊天消息",
        "chat_thread": "聊天话题",
        "minutes_segment": "妙记片段",
        "meeting": "会议",
        "web_section": "网页章节",
        "whiteboard_node": "白板节点",
        "local_span": "本地文件片段",
    }
    locator = anchor.get("locator", {})
    parts = []
    anchor_id = str(anchor.get("anchor_id", "")).strip()
    if anchor_id:
        parts.append(f"anchor_id={anchor_id}")
    for key in (
        "heading",
        "block_id",
        "comment_id",
        "reply_id",
        "chat_id",
        "message_id",
        "thread_id",
        "start_ms",
        "line_start",
        "line_end",
    ):
        value = str(locator.get(key, "")).strip() if isinstance(locator, dict) else ""
        if value:
            parts.append(f"{key}={value}")
    label = str(anchor.get("label", "")).strip()
    if label:
        parts.insert(0, label)
    prefix = kind_labels.get(str(anchor.get("kind", "")), str(anchor.get("kind", "")))
    return " · ".join([prefix, *parts])


def materialize_node_provenance(
    content: str,
    node_relative_path: str,
    primary_source: str,
    primary_source_url: str,
    evidence: Sequence[Mapping[str, Any]],
    raw_records: Mapping[str, Mapping[str, Any]],
) -> str:
    updated = set_frontmatter_scalars(
        content,
        {
            "primary_source": primary_source,
            "primary_source_url": primary_source_url,
        },
    )
    frontmatter, body = parse_frontmatter(updated)
    sources = set(list_value(frontmatter.get("sources")))
    if primary_source and primary_source not in sources:
        raise ProvenanceError(
            f"primary_source {primary_source} 不在节点 sources 中"
        )

    base_body = strip_evidence_section(body)
    markers = set(EVIDENCE_MARKER_RE.findall(base_body))
    ids = [str(item.get("id", "")).strip() for item in evidence]
    if len(ids) != len(set(ids)):
        raise ProvenanceError("节点 evidence 包含重复 id")
    for evidence_id in ids:
        if not EVIDENCE_ID_RE.fullmatch(evidence_id):
            raise ProvenanceError(f"非法 evidence id: {evidence_id!r}")
    if markers != set(ids):
        missing = sorted(set(ids) - markers)
        unknown = sorted(markers - set(ids))
        raise ProvenanceError(
            "节点正文与 evidence 映射不一致: "
            f"正文缺少={missing or '无'}, 未登记={unknown or '无'}"
        )
    if not evidence:
        return updated

    rows = [
        "## 证据",
        "",
        "| 编号 | 原始来源 | 定位 | 原文时间 | 收录时间 | 精度 |",
        "|---|---|---|---|---|---|",
    ]
    definitions: List[Tuple[str, str]] = []
    node_dir = posixpath.dirname(node_relative_path)
    for item in evidence:
        evidence_id = str(item["id"])
        raw_id = str(item.get("raw_id", "")).strip()
        anchor = item.get("anchor")
        if raw_id not in sources:
            raise ProvenanceError(
                f"{evidence_id} 引用的 {raw_id} 不在节点 sources 中"
            )
        if not isinstance(anchor, dict):
            raise ProvenanceError(f"{evidence_id} 缺少已解析 anchor")
        raw = raw_records.get(raw_id)
        if not raw:
            raise ProvenanceError(f"{evidence_id} 找不到 raw: {raw_id}")
        raw_fm = raw["frontmatter"]
        target = str(anchor.get("open_url") or anchor.get("fallback_url") or "")
        if not target:
            target = posixpath.relpath(str(raw["relative_path"]), node_dir)
        definitions.append((evidence_id, target))
        title = source_title_from_frontmatter(raw_fm)
        source_cell = f"[{_cell(title)}][{evidence_id}] · `{_cell(raw_id)}`"
        rows.append(
            "| "
            + " | ".join(
                [
                    f"**[{evidence_id}]**",
                    source_cell,
                    _cell(_locator_summary(anchor)),
                    _cell(anchor.get("source_time")),
                    _cell(raw_fm.get("ingested")),
                    _cell(anchor.get("precision")),
                ]
            )
            + " |"
        )
    rows.extend([""])
    for evidence_id, target in definitions:
        rows.append(f"[{evidence_id}]: <{target}>")

    fm_match = re.match(r"^---\r?\n[\s\S]*?\r?\n---\r?\n?", updated)
    if not fm_match:
        raise ProvenanceError("节点 frontmatter 无法重新组装")
    frontmatter_text = updated[: fm_match.end()]
    final_body = base_body.rstrip() + "\n\n" + "\n".join(rows).rstrip() + "\n"
    return frontmatter_text + final_body
