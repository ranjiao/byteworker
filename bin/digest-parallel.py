#!/usr/bin/env python3
"""CLI for bounded digest analysis shard planning and reduction."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from digest_parallel import (  # noqa: E402
    DigestParallelError,
    attempt_parallel_shard,
    checkpoint_parallel_results,
    merge_parallel_results,
    plan_parallel_work,
)
from digest_run_log import SHARD_FAILURE_CODES, DigestRunError  # noqa: E402
from workflow_budget import WorkflowBudgetError  # noqa: E402


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="plan and reduce bounded digest workers")
    sub = result.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--stage", choices=("dependency", "semantic", "conflict"), required=True)
    plan.add_argument("--input", required=True, type=Path)
    plan.add_argument("--out-dir", required=True, type=Path)
    plan.add_argument("--max-workers", type=int, choices=range(1, 5), default=4)
    merge = sub.add_parser("merge")
    merge.add_argument("--plan", required=True, type=Path)
    merge.add_argument("--result", required=True, action="append", type=Path)
    merge.add_argument("--out", required=True, type=Path)
    checkpoint = sub.add_parser("checkpoint")
    checkpoint.add_argument("--kb", required=True, type=Path)
    checkpoint.add_argument("--plan", required=True, type=Path)
    checkpoint.add_argument("--result", type=Path)
    attempt = sub.add_parser("attempt", help="Check one shard's budget or record a host execution failure")
    attempt.add_argument("--kb", required=True, type=Path)
    attempt.add_argument("--plan", required=True, type=Path)
    attempt.add_argument("--shard-id", required=True)
    attempt.add_argument("--failure", choices=sorted(SHARD_FAILURE_CODES), default="")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "plan":
            value = plan_parallel_work(
                args.input,
                args.out_dir,
                stage=args.stage,
                max_workers=args.max_workers,
            )
        elif args.command == "merge":
            value = merge_parallel_results(args.plan, args.result, args.out)
        elif args.command == "checkpoint":
            value = checkpoint_parallel_results(args.kb, args.plan, result_path=args.result)
        else:
            value = attempt_parallel_shard(args.kb, args.plan, shard_id=args.shard_id, failure=args.failure)
    except (DigestParallelError, DigestRunError, WorkflowBudgetError) as exc:
        json.dump({"error": exc.as_dict()}, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        return 1
    json.dump(value, sys.stdout, ensure_ascii=False, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
