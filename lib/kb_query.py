"""Deterministic, dependency-free query helpers for a byteworker KB."""

from __future__ import annotations

import difflib
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from frontmatter import parse_file, parse_frontmatter
from provenance import ProvenanceError, anchor_index, scan_provenance, scan_raws
from sources import STRUCTURED_SOURCE_TYPES, project_legacy_record


class QueryError(RuntimeError):
    pass


SNAPSHOT_SCHEMA = "byteworker-source-snapshot/v1"
RECORD_INDEX_SCHEMA = "byteworker-record-index/v1"


def _list_value(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip().strip("'\"") for item in value if str(item).strip()]
    if value:
        return [str(value).strip()]
    return []


def _tokens(query: str) -> List[str]:
    query = re.sub(r"\s+", " ", query.strip().lower())
    if not query:
        raise QueryError("query 不能为空")
    parts = re.findall(r"[a-z0-9_.:+-]+|[\u3400-\u9fff]+", query)
    return list(dict.fromkeys([query, *parts]))


def _node_records(kb: Path) -> Dict[str, Dict[str, Any]]:
    records: Dict[str, Dict[str, Any]] = {}
    for path in sorted((kb / "knowledge").glob("**/*.md")):
        frontmatter, body = parse_file(str(path))
        node_id = str(frontmatter.get("id", "")).strip()
        if not node_id:
            continue
        if node_id in records:
            raise QueryError(f"知识库存在重复节点 id: {node_id}")
        tldr_match = re.search(r"(?m)^>\s*\*\*TL;DR:\*\*\s*(.+)$", body)
        records[node_id] = {
            "id": node_id,
            "title": str(frontmatter.get("title", "")).strip(),
            "type": str(frontmatter.get("type", "")).strip(),
            "tags": _list_value(frontmatter.get("tags")),
            "links": _list_value(frontmatter.get("links")),
            "sources": _list_value(frontmatter.get("sources")),
            "tldr": tldr_match.group(1).strip() if tldr_match else "",
            "body": body,
            "path": str(path.relative_to(kb)),
        }
    return records


def search(
    kb: Path,
    query: str,
    *,
    limit: int = 12,
    graph_depth: int = 1,
    max_nodes: int = 30,
) -> Dict[str, Any]:
    if limit < 1 or max_nodes < 1:
        raise QueryError("limit/max_nodes 必须大于 0")
    if graph_depth not in {0, 1}:
        raise QueryError("轻量查询仅支持 graph_depth=0 或 1")
    kb = kb.resolve()
    records = _node_records(kb)
    tokens = _tokens(query)
    ranked: List[Tuple[int, str, List[str]]] = []
    weights = (
        ("id", 12),
        ("title", 10),
        ("tags", 7),
        ("tldr", 5),
        ("body", 1),
    )
    for node_id, record in records.items():
        score = 0
        reasons: List[str] = []
        for field, weight in weights:
            value = record[field]
            text = " ".join(value) if isinstance(value, list) else str(value)
            lowered = text.lower()
            matched = [token for token in tokens if token and token in lowered]
            if matched:
                score += weight * len(matched)
                reasons.append(f"{field}:{','.join(matched[:3])}")
        if score:
            ranked.append((score, node_id, reasons))
    ranked.sort(key=lambda item: (-item[0], item[1]))

    selected: List[Tuple[int, str, List[str]]] = ranked[: min(limit, max_nodes)]
    selected_ids = {item[1] for item in selected}
    graph_added = 0
    if graph_depth == 1 and len(selected) < max_nodes:
        adjacency: Dict[str, set[str]] = {node_id: set() for node_id in records}
        for node_id, record in records.items():
            for target in record["links"]:
                if target in records:
                    adjacency[node_id].add(target)
                    adjacency[target].add(node_id)
        for _, seed_id, _ in list(selected):
            for neighbor in sorted(adjacency.get(seed_id, set())):
                if neighbor in selected_ids:
                    continue
                selected.append((0, neighbor, [f"graph:{seed_id}"]))
                selected_ids.add(neighbor)
                graph_added += 1
                if len(selected) >= max_nodes:
                    break
            if len(selected) >= max_nodes:
                break

    candidates = []
    for score, node_id, reasons in selected:
        record = records[node_id]
        candidates.append(
            {
                "id": node_id,
                "title": record["title"],
                "type": record["type"],
                "path": record["path"],
                "score": score,
                "reasons": reasons,
                "tldr": record["tldr"],
                "sources": record["sources"],
                "links": record["links"],
            }
        )
    return {
        "query": query,
        "coverage": {
            "scanned_nodes": len(records),
            "text_matches": len(ranked),
            "seed_limit": limit,
            "graph_depth": graph_depth,
            "graph_added": graph_added,
            "max_nodes": max_nodes,
            "returned": len(candidates),
            "truncated": len(ranked) > limit or (
                graph_depth == 1 and len(candidates) >= max_nodes
            ),
        },
        "candidates": candidates,
    }


def _frontmatter_only(path: Path) -> Dict[str, Any]:
    """Read only the small YAML header, without loading a potentially huge raw body."""
    lines: List[str] = []
    with path.open("r", encoding="utf-8") as handle:
        first = handle.readline()
        if first.strip() != "---":
            return {}
        lines.append(first.rstrip("\n"))
        for line in handle:
            lines.append(line.rstrip("\n"))
            if line.strip() == "---":
                break
        else:
            return {}
    frontmatter, _ = parse_frontmatter("\n".join(lines))
    return frontmatter


def _ingested_sort_key(value: Any) -> Tuple[int, float, str]:
    text = str(value or "").strip()
    if not text:
        return (0, 0.0, "")
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return (1, parsed.timestamp(), text)
    except ValueError:
        return (0, 0.0, text)


def _structured_raw_inventory(
    kb: Path,
    *,
    source_type: str = "",
    source_uid: str = "",
) -> List[Dict[str, Any]]:
    inventory: List[Dict[str, Any]] = []
    for path in sorted((kb / "raw_data").glob("*.md")):
        frontmatter = _frontmatter_only(path)
        raw_source_type = str(frontmatter.get("source_type", "")).strip()
        raw_source_uid = str(frontmatter.get("source_uid", "")).strip()
        if raw_source_type not in STRUCTURED_SOURCE_TYPES or not raw_source_uid:
            continue
        if source_type and raw_source_type != source_type:
            continue
        if source_uid and raw_source_uid != source_uid:
            continue
        digest_status = str(frontmatter.get("digest_status", "")).strip()
        if digest_status and digest_status != "digested":
            continue
        inventory.append(
            {
                "path": path,
                "relative_path": str(path.relative_to(kb)),
                "frontmatter": frontmatter,
                "source_type": raw_source_type,
                "source_uid": raw_source_uid,
                "sort_key": (
                    *_ingested_sort_key(frontmatter.get("ingested")),
                    path.name,
                ),
            }
        )
    return inventory


def _json_code_blocks(body: str) -> Iterable[Any]:
    lines = body.splitlines()
    index = 0
    while index < len(lines):
        match = re.fullmatch(r"(`{3,})json\s*", lines[index].strip())
        if not match:
            index += 1
            continue
        fence = match.group(1)
        start = index + 1
        index = start
        while index < len(lines) and lines[index].strip() != fence:
            index += 1
        if index >= len(lines):
            raise QueryError("raw 中的 JSON code fence 未闭合")
        text = "\n".join(lines[start:index]).strip()
        if text:
            try:
                yield json.loads(text)
            except json.JSONDecodeError as exc:
                raise QueryError(
                    f"raw 中的结构化 JSON 无法解析: line {exc.lineno} column {exc.colno}"
                ) from exc
        index += 1


def _snapshot_from_raw(path: Path) -> Mapping[str, Any]:
    _, body = parse_file(str(path))
    for value in _json_code_blocks(body):
        candidate = value.get("snapshot") if isinstance(value, Mapping) else None
        if isinstance(candidate, Mapping):
            value = candidate
        if not isinstance(value, Mapping):
            continue
        records = value.get("records")
        if value.get("schema_version") == SNAPSHOT_SCHEMA and isinstance(records, list):
            return value
        if isinstance(records, list) and value.get("source_type") in STRUCTURED_SOURCE_TYPES:
            return value
    raise QueryError("raw 中找不到 byteworker-source-snapshot/v1 完整快照")


def _record_index_from_raw(path: Path) -> Mapping[str, Any] | None:
    _, body = parse_file(str(path))
    for value in _json_code_blocks(body):
        if (
            isinstance(value, Mapping)
            and value.get("schema_version") == RECORD_INDEX_SCHEMA
            and isinstance(value.get("records"), list)
        ):
            return value
    return None


def _record_anchor(kb: Path, raw_id: str, anchor_id: str) -> Mapping[str, Any]:
    if not raw_id or "/" in raw_id or "\\" in raw_id:
        return {}
    path = kb / "provenance" / f"{raw_id}.json"
    if not path.is_file():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    anchors = document.get("anchors") if isinstance(document, Mapping) else None
    if not isinstance(anchors, list):
        return {}
    for anchor in anchors:
        if (
            isinstance(anchor, Mapping)
            and str(anchor.get("anchor_id", "")).strip() == anchor_id
        ):
            return anchor
    return {}


def _normalize_title(value: str) -> Tuple[str, str]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    spaced = "".join(
        character
        if unicodedata.category(character)[0] in {"L", "N"}
        else " "
        for character in normalized
    )
    words = " ".join(spaced.split())
    return words, words.replace(" ", "")


def _bigram_dice(left: str, right: str) -> float:
    if len(left) < 2 or len(right) < 2:
        return 0.0
    left_pairs = {left[index:index + 2] for index in range(len(left) - 1)}
    right_pairs = {right[index:index + 2] for index in range(len(right) - 1)}
    return 2 * len(left_pairs & right_pairs) / (len(left_pairs) + len(right_pairs))


def _title_score(query: str, candidate: str) -> Tuple[float, str]:
    raw_query = unicodedata.normalize("NFKC", query).casefold().strip()
    raw_candidate = unicodedata.normalize("NFKC", candidate).casefold().strip()
    query_words, query_compact = _normalize_title(query)
    candidate_words, candidate_compact = _normalize_title(candidate)
    if not query_compact:
        raise QueryError("title 归一化后为空，请提供至少一个文字或数字")
    if not candidate_compact:
        return 0.0, "empty"
    if raw_query == raw_candidate:
        return 1.0, "exact"
    if query_words == candidate_words or query_compact == candidate_compact:
        return 0.99, "normalized_exact"
    scores: List[Tuple[float, str]] = []
    if query_compact in candidate_compact:
        ratio = len(query_compact) / len(candidate_compact)
        scores.append((0.90 + 0.09 * ratio, "contains"))
    if candidate_compact in query_compact:
        ratio = len(candidate_compact) / len(query_compact)
        scores.append((0.86 + 0.10 * ratio, "contained_by"))
    sequence = difflib.SequenceMatcher(
        None, query_compact, candidate_compact, autojunk=False
    ).ratio()
    dice = _bigram_dice(query_compact, candidate_compact)
    scores.append((0.65 * sequence + 0.35 * dice, "fuzzy"))
    query_tokens = query_words.split()
    if query_tokens:
        covered = sum(token in candidate_compact for token in query_tokens)
        if covered:
            coverage = covered / len(query_tokens)
            scores.append((0.52 + 0.43 * coverage, "token_coverage"))
    return max(scores, default=(0.0, "none"))


def source_records(
    kb: Path,
    *,
    source_type: str = "",
    source_uid: str = "",
    record_id: str = "",
    title: str = "",
    title_threshold: float = 0.55,
    limit: int = 5,
    history: bool = False,
) -> Dict[str, Any]:
    """Search records inside canonical Meego/Base raw snapshots."""
    kb = kb.resolve()
    source_type = source_type.strip()
    source_uid = source_uid.strip()
    record_id = record_id.strip()
    title = title.strip()
    if source_type and source_type not in STRUCTURED_SOURCE_TYPES:
        raise QueryError(f"不支持 source_type={source_type}")
    if not record_id and not title:
        raise QueryError("record-id 与 title 至少提供一个")
    if not 0 <= title_threshold <= 1:
        raise QueryError("title-threshold 必须位于 0 到 1")
    if not 1 <= limit <= 50:
        raise QueryError("limit 必须位于 1 到 50")
    if title:
        _normalize_words, normalized_title = _normalize_title(title)
        if not normalized_title:
            raise QueryError("title 归一化后为空，请提供至少一个文字或数字")

    inventory = _structured_raw_inventory(
        kb,
        source_type=source_type,
        source_uid=source_uid,
    )
    latest_by_source: Dict[str, Dict[str, Any]] = {}
    for item in inventory:
        current = latest_by_source.get(item["source_uid"])
        if current is None or item["sort_key"] > current["sort_key"]:
            latest_by_source[item["source_uid"]] = item
    selected = inventory if history else list(latest_by_source.values())
    selected.sort(key=lambda item: item["sort_key"], reverse=True)

    matches: List[Dict[str, Any]] = []
    parse_warnings: List[Dict[str, str]] = []
    missing_anchors: set[str] = set()
    scanned_records = 0
    parsed_snapshots = 0
    for item in selected:
        try:
            snapshot = _snapshot_from_raw(item["path"])
            record_index = _record_index_from_raw(item["path"])
        except QueryError as exc:
            parse_warnings.append(
                {"raw_path": item["relative_path"], "message": str(exc)}
            )
            continue
        payload = record_index or snapshot
        records = payload.get("records")
        if not isinstance(records, list):
            continue
        parsed_snapshots += 1
        snapshot_source_type = str(
            payload.get("source_type") or item["source_type"]
        ).strip()
        snapshot_source_uid = str(
            payload.get("source_uid") or item["source_uid"]
        ).strip()
        for record in records:
            scanned_records += 1
            if record_index is not None and isinstance(record, Mapping):
                candidate_id = str(record.get("record_id", "")).strip()
                candidate_title = str(record.get("title", "")).strip()
                projection = {
                    "record_id": candidate_id,
                    "title_candidates": (
                        [("title", candidate_title, 1.0)]
                        if candidate_title
                        else []
                    ),
                    "anchor_id": str(record.get("anchor_id", "")).strip()
                    or f"record:{candidate_id}",
                }
            else:
                projection = project_legacy_record(record, snapshot_source_type)
            candidate_id = projection["record_id"]
            if not candidate_id:
                continue
            if record_id and candidate_id != record_id:
                continue
            candidates = projection["title_candidates"]
            best_score = 1.0 if record_id and not title else 0.0
            best_kind = "record_id" if record_id and not title else ""
            best_field = ""
            best_title = candidates[0][1] if candidates else ""
            if title:
                for field, candidate_title, preference in candidates:
                    score, kind = _title_score(title, candidate_title)
                    score *= preference
                    if score > best_score:
                        best_score = score
                        best_kind = kind
                        best_field = field
                        best_title = candidate_title
                if best_score < title_threshold:
                    continue
            is_latest = latest_by_source.get(item["source_uid"]) is item
            frontmatter = item["frontmatter"]
            raw_id = str(frontmatter.get("raw_id", "")).strip()
            anchor_id = projection["anchor_id"]
            anchor = _record_anchor(kb, raw_id, anchor_id)
            if not anchor:
                missing_anchors.add(f"{raw_id}#{anchor_id}")
            matches.append(
                {
                    "source_type": snapshot_source_type,
                    "source_uid": snapshot_source_uid,
                    "record_id": candidate_id,
                    "title": best_title,
                    "match": {
                        "kind": best_kind,
                        "score": round(best_score, 4),
                        "field": best_field,
                    },
                    "record": record,
                    "provenance": {
                        "raw_id": raw_id,
                        "raw_path": item["relative_path"],
                        "ingested": str(frontmatter.get("ingested", "")).strip(),
                        "source_title": str(
                            frontmatter.get("source_title", "")
                        ).strip(),
                        "source_url": str(frontmatter.get("source_url", "")).strip(),
                        "anchor_id": anchor_id,
                        "anchor": anchor,
                        "is_latest_snapshot": is_latest,
                    },
                    "_sort_key": (
                        best_score,
                        1 if is_latest else 0,
                        item["sort_key"],
                    ),
                }
            )

    matches.sort(key=lambda item: item["_sort_key"], reverse=True)
    total_matches = len(matches)
    for item in matches:
        item.pop("_sort_key", None)
    returned = matches[:limit]
    return {
        "query": {
            "source_type": source_type or None,
            "source_uid": source_uid or None,
            "record_id": record_id or None,
            "title": title or None,
            "title_threshold": title_threshold if title else None,
            "history": history,
        },
        "coverage": {
            "scanned_raw_files": len(list((kb / "raw_data").glob("*.md"))),
            "structured_raw_files": len(inventory),
            "selected_snapshots": len(selected),
            "parsed_snapshots": parsed_snapshots,
            "scanned_records": scanned_records,
            "text_matches": total_matches,
            "returned": len(returned),
            "truncated": total_matches > limit,
            "parse_warnings": parse_warnings,
            "missing_anchors": sorted(missing_anchors),
        },
        "ambiguous": total_matches > 1,
        "matches": returned,
    }


def _evidence_rows(body: str) -> Dict[str, Dict[str, str]]:
    match = re.search(r"(?ms)^## 证据\s*$([\s\S]*)", body)
    if not match:
        return {}
    section = match.group(1)
    targets = {
        marker: target
        for marker, target in re.findall(
            r"(?m)^\[(E[1-9][0-9]*)\]:\s*<([^>]+)>\s*$", section
        )
    }
    rows: Dict[str, Dict[str, str]] = {}
    for line in section.splitlines():
        marker_match = re.match(r"^\|\s*\*\*\[(E[1-9][0-9]*)\]\*\*\s*\|", line)
        if not marker_match:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 6:
            continue
        marker = marker_match.group(1)
        raw_match = re.search(r"`([^`]+)`", cells[1])
        anchor_match = re.search(r"anchor_id=([^\s·]+)", cells[2])
        rows[marker] = {
            "id": marker,
            "raw_id": raw_match.group(1) if raw_match else "",
            "anchor_id": anchor_match.group(1) if anchor_match else "",
            "locator": cells[2],
            "source_time": cells[3],
            "ingested": cells[4],
            "precision": cells[5],
            "target": targets.get(marker, ""),
        }
    return rows


def evidence(
    kb: Path,
    node_id: str,
    markers: Sequence[str] = (),
) -> Dict[str, Any]:
    kb = kb.resolve()
    records = _node_records(kb)
    record = records.get(node_id)
    if not record:
        raise QueryError(f"找不到节点: {node_id}")
    path = kb / record["path"]
    frontmatter, body = parse_file(str(path))
    rows = _evidence_rows(body)
    wanted = list(dict.fromkeys(marker.strip() for marker in markers if marker.strip()))
    if not wanted:
        wanted = sorted(rows, key=lambda value: int(value[1:]))
    unknown = sorted(set(wanted) - set(rows))
    if unknown:
        raise QueryError(f"节点 {node_id} 没有这些证据标记: {unknown}")

    try:
        raws = scan_raws(kb)
        provenance = scan_provenance(kb)
    except ProvenanceError as exc:
        raise QueryError(str(exc)) from exc
    resolved = []
    for marker in wanted:
        row = dict(rows[marker])
        raw_id = row["raw_id"]
        raw = raws.get(raw_id)
        document = provenance.get(raw_id)
        anchor = (
            anchor_index(document).get(row["anchor_id"])
            if document and row["anchor_id"]
            else None
        )
        row["raw_path"] = str(raw["relative_path"]) if raw else ""
        row["source_url"] = (
            str(
                raw["frontmatter"].get("source_url")
                or raw["frontmatter"].get("recording_url")
                or ""
            )
            if raw
            else ""
        )
        row["anchor"] = anchor or {}
        resolved.append(row)
    return {
        "node": {
            "id": node_id,
            "title": str(frontmatter.get("title", "")).strip(),
            "type": str(frontmatter.get("type", "")).strip(),
            "path": record["path"],
            "primary_source": str(frontmatter.get("primary_source", "")).strip(),
            "primary_source_url": str(
                frontmatter.get("primary_source_url", "")
            ).strip(),
        },
        "requested_markers": wanted,
        "evidence": resolved,
        "coverage": {
            "available_markers": sorted(rows, key=lambda value: int(value[1:])),
            "returned": len(resolved),
            "missing_sidecars": sorted(
                {
                    item["raw_id"]
                    for item in resolved
                    if item["raw_id"] and not item["anchor"]
                }
            ),
        },
    }
