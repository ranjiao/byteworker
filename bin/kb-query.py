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

from kb_query import QueryError, evidence, search, source_records  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="byteworker deterministic KB query")
    subparsers = parser.add_subparsers(dest="command", required=True)

    search_parser = subparsers.add_parser("search")
    search_parser.add_argument("--kb", required=True, type=Path)
    search_parser.add_argument("--query", required=True)
    search_parser.add_argument("--limit", type=int, default=12)
    search_parser.add_argument("--graph-depth", type=int, default=1)
    search_parser.add_argument("--max-nodes", type=int, default=30)

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
