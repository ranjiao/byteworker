#!/usr/bin/env python3
"""Small deterministic search/evidence CLI for byteworker knowledge bases."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from kb_query import (  # noqa: E402
    QueryError,
    conflict_search,
    evidence,
    search,
    source_records,
)


def _private_request(path: Path) -> dict:
    resolved = path.expanduser().resolve(strict=False)
    if resolved == ROOT or ROOT in resolved.parents:
        raise QueryError(
            "conflict query 可能包含业务事实，必须位于系统临时目录或知识库，不能写进 skill 仓库"
        )
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QueryError(f"无法读取 conflict query: {resolved}") from exc
    if not isinstance(value, dict):
        raise QueryError("conflict query 顶层必须是对象")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="byteworker deterministic KB query")
    subparsers = parser.add_subparsers(dest="command", required=True)

    search_parser = subparsers.add_parser("search")
    search_parser.add_argument("--kb", required=True, type=Path)
    search_parser.add_argument("--query", required=True)
    search_parser.add_argument("--limit", type=int, default=12)
    search_parser.add_argument("--graph-depth", type=int, default=1)
    search_parser.add_argument("--max-nodes", type=int, default=30)

    conflict_parser = subparsers.add_parser(
        "conflict-search",
        help="对候选事实做单次 KB 扫描和有界冲突候选召回，不做语义裁决",
    )
    conflict_parser.add_argument("--kb", required=True, type=Path)
    conflict_parser.add_argument("--request", required=True, type=Path)
    conflict_parser.add_argument("--limit-per-query", type=int, default=3)
    conflict_parser.add_argument("--max-nodes", type=int, default=20)
    conflict_parser.add_argument("--max-snippet-chars", type=int, default=800)

    evidence_parser = subparsers.add_parser("evidence")
    evidence_parser.add_argument("--kb", required=True, type=Path)
    evidence_parser.add_argument("--node", required=True)
    evidence_parser.add_argument(
        "--markers",
        default="",
        help="逗号分隔的 E1,E2；省略时返回节点全部证据",
    )

    source_parser = subparsers.add_parser(
        "source-record",
        help="从 Meego / Base / 风神完整 raw 快照确定性检索结构化记录",
    )
    source_parser.add_argument("--kb", required=True, type=Path)
    source_parser.add_argument(
        "--source-type",
        choices=("meego", "feishu_base", "aeolus"),
        default="",
    )
    source_parser.add_argument("--source-uid", default="")
    source_parser.add_argument("--record-id", default="")
    source_parser.add_argument("--title", default="")
    source_parser.add_argument("--title-threshold", type=float, default=0.55)
    source_parser.add_argument("--limit", type=int, default=5)
    source_parser.add_argument(
        "--history",
        action="store_true",
        help="同时检索历史快照；默认每个 source_uid 只查最新版本",
    )

    args = parser.parse_args()
    try:
        if args.command == "search":
            output = search(
                args.kb,
                args.query,
                limit=args.limit,
                graph_depth=args.graph_depth,
                max_nodes=args.max_nodes,
            )
        elif args.command == "conflict-search":
            output = conflict_search(
                args.kb,
                _private_request(args.request),
                limit_per_query=args.limit_per_query,
                max_nodes=args.max_nodes,
                max_snippet_chars=args.max_snippet_chars,
            )
        elif args.command == "evidence":
            output = evidence(
                args.kb,
                args.node,
                [item.strip() for item in args.markers.split(",") if item.strip()],
            )
        else:
            output = source_records(
                args.kb,
                source_type=args.source_type,
                source_uid=args.source_uid,
                record_id=args.record_id,
                title=args.title,
                title_threshold=args.title_threshold,
                limit=args.limit,
                history=args.history,
            )
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except (OSError, QueryError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
