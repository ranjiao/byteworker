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
    replan_parallel_work,
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
    plan.add_argument("--host-input-limit", type=int, help="Host input tokens available for rules and one shard")
    replan = sub.add_parser("replan", help="Resize pending shards and preserve verified checkpoints")
    replan.add_argument("--kb", required=True, type=Path)
    replan.add_argument("--plan", required=True, type=Path)
    replan_mode = replan.add_mutually_exclusive_group(required=True)
    replan_mode.add_argument("--host-input-limit", type=int)
    replan_mode.add_argument("--failed-attempt-id", default="", help="Resize using a verified host context rejection")
    replan.add_argument("--max-workers", type=int, choices=range(1, 5), default=1)
    merge = sub.add_parser("merge")
    merge.add_argument("--plan", required=True, type=Path)
    merge.add_argument("--result", required=True, action="append", type=Path)
    merge.add_argument("--out", required=True, type=Path)
    checkpoint = sub.add_parser("checkpoint")
    checkpoint.add_argument("--kb", required=True, type=Path)
    checkpoint.add_argument("--plan", required=True, type=Path)
    checkpoint.add_argument("--result", type=Path)
    attempt = sub.add_parser("attempt", help="Check one shard's budget or verify a host execution failure")
    attempt.add_argument("--kb", required=True, type=Path)
    attempt.add_argument("--plan", required=True, type=Path)
    attempt.add_argument("--shard-id", required=True)
    attempt.add_argument("--failure", choices=sorted(SHARD_FAILURE_CODES), default="")
    attempt.add_argument("--attempt-id", default="")
    attempt.add_argument("--host-session-id", default="")
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
                host_input_limit=args.host_input_limit,
            )
        elif args.command == "replan":
            value = replan_parallel_work(
                args.kb, args.plan, host_input_limit=args.host_input_limit,
                failed_attempt_id=args.failed_attempt_id, max_workers=args.max_workers,
            )
        elif args.command == "merge":
            value = merge_parallel_results(args.plan, args.result, args.out)
        elif args.command == "checkpoint":
            value = checkpoint_parallel_results(args.kb, args.plan, result_path=args.result)
        else:
            value = attempt_parallel_shard(
                args.kb, args.plan, shard_id=args.shard_id, failure=args.failure,
                attempt_id=args.attempt_id, host_session_id=args.host_session_id,
            )
    except (DigestParallelError, DigestRunError, WorkflowBudgetError) as exc:
        json.dump({"error": exc.as_dict()}, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        return 1
    json.dump(value, sys.stdout, ensure_ascii=False, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
