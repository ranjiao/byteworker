#!/usr/bin/env python3
"""Inspect complete workflow instruction and dynamic token budgets."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from workflow_budget import (  # noqa: E402
    WorkflowBudgetError,
    inspect_budget,
    load_manifest,
    resolve_route,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="resolve workflow rules or inspect token budgets")
    sub = result.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workflow", required=True)
    common.add_argument("--source-type", default="")
    common.add_argument("--feature", action="append", default=[])
    common.add_argument("--include-on-error", action="store_true")
    sub.add_parser("resolve", parents=[common], help="return only selected rule paths")
    inspect = sub.add_parser("inspect", parents=[common], help="measure complete token budgets")
    inspect.add_argument("--context", type=Path)
    inspect.add_argument("--source-packet", type=Path)
    inspect.add_argument("--approved-review-id", default="", help="仅在用户明确确认本次预算例外后使用")
    inspect.add_argument("--host-input-limit", type=int, help="核实后的宿主可用输入 token 数")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        route_args = dict(
            workflow=args.workflow,
            source_type=args.source_type,
            features=args.feature,
            include_on_error=args.include_on_error,
        )
        if args.command == "resolve":
            value = {
                "schema_version": "byteworker-workflow-route/v1",
                **resolve_route(load_manifest(), **route_args),
            }
        else:
            value = inspect_budget(
                **route_args,
                context_path=args.context,
                source_packet_path=args.source_packet,
                approved_review_id=args.approved_review_id,
                host_input_limit=args.host_input_limit,
            )
    except WorkflowBudgetError as exc:
        json.dump({"error": exc.as_dict()}, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        return 1
    json.dump(value, sys.stdout, ensure_ascii=False, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
