"""Deterministic, dependency-free query helpers for a byteworker KB."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from frontmatter import parse_file
from provenance import ProvenanceError, anchor_index, scan_provenance, scan_raws


class QueryError(RuntimeError):
    pass


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
