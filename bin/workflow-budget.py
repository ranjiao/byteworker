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

from workflow_budget import WorkflowBudgetError, inspect_budget  # noqa: E402


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="inspect complete workflow token budgets")
    sub = result.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("--workflow", required=True)
    inspect.add_argument("--source-type", default="")
    inspect.add_argument("--feature", action="append", default=[])
    inspect.add_argument("--include-on-error", action="store_true")
    inspect.add_argument("--context", type=Path)
    inspect.add_argument("--source-packet", type=Path)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        value = inspect_budget(
            workflow=args.workflow,
            source_type=args.source_type,
            features=args.feature,
            include_on_error=args.include_on_error,
            context_path=args.context,
            source_packet_path=args.source_packet,
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
