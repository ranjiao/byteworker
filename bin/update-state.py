#!/usr/bin/env python3
"""Internal state helper for bin/update-check.sh."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from update_state import (  # noqa: E402
    load_state,
    mark_postflight_pending,
    postflight_due,
    record_attempt,
    record_failure,
    record_postflight_failure,
    record_postflight_success,
    record_success,
    update_due,
    write_state,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="byteworker update state helper")
    result.add_argument("--state", required=True, type=Path)
    result.add_argument("--legacy-stamp", type=Path)
    result.add_argument("--now", required=True, type=int)
    result.add_argument("--interval", type=int, default=7 * 24 * 3600)
    result.add_argument("--retry-base", type=int, default=60 * 60)
    result.add_argument("--retry-max", type=int, default=6 * 60 * 60)
    result.add_argument("--postflight-retry-base", type=int, default=5 * 60)
    result.add_argument("--postflight-retry-max", type=int, default=60 * 60)
    sub = result.add_subparsers(dest="command", required=True)
    due = sub.add_parser("due")
    due.add_argument("--force", action="store_true")
    sub.add_parser("attempt")
    success = sub.add_parser("success")
    success.add_argument("--commit", default="")
    failure = sub.add_parser("failure")
    failure.add_argument("--code", required=True)
    pending = sub.add_parser("postflight-pending")
    pending.add_argument("--commit", default="")
    sub.add_parser("postflight-due")
    sub.add_parser("postflight-success")
    postflight_failure = sub.add_parser("postflight-failure")
    postflight_failure.add_argument("--code", required=True)
    sub.add_parser("status")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    state = load_state(args.state, args.legacy_stamp)
    if args.command == "due":
        return (
            0
            if update_due(
                state,
                now=args.now,
                interval=args.interval,
                force=args.force,
            )
            else 10
        )
    if args.command == "postflight-due":
        return 0 if postflight_due(state, now=args.now) else 10
    if args.command == "status":
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return 0
    if args.command == "attempt":
        state = record_attempt(state, now=args.now)
    elif args.command == "success":
        state = record_success(state, now=args.now, commit=args.commit)
    elif args.command == "failure":
        state = record_failure(
            state,
            now=args.now,
            code=args.code,
            retry_base=args.retry_base,
            retry_max=args.retry_max,
        )
    elif args.command == "postflight-pending":
        state = mark_postflight_pending(state, commit=args.commit)
    elif args.command == "postflight-success":
        state = record_postflight_success(state)
    elif args.command == "postflight-failure":
        state = record_postflight_failure(
            state,
            now=args.now,
            code=args.code,
            retry_base=args.postflight_retry_base,
            retry_max=args.postflight_retry_max,
        )
    write_state(args.state, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
