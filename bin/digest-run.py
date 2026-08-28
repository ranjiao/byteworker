#!/usr/bin/env python3
"""CLI for structured digest timing logs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from digest_run_log import (  # noqa: E402
    DigestRunError,
    METRIC_FIELDS,
    RUN_STATUSES,
    SOURCE_TYPES,
    STAGE_ACTIONS,
    STAGE_STATUSES,
    USAGE_FIELDS,
    USAGE_SOURCES,
    WAIT_REASONS,
    WORKER_ROLES,
    finish_run,
    list_runs,
    record_heartbeat,
    record_stage,
    record_usage,
    resume_run,
    show_run,
    start_run,
    wait_for_user,
)


def configured_kb() -> str:
    config = ROOT / ".kbconfig"
    if not config.is_file():
        return ""
    return config.read_text(encoding="utf-8").splitlines()[0].strip()


def _add_kb(command: argparse.ArgumentParser) -> None:
    command.add_argument("--kb", default=configured_kb(), help="knowledge base directory")


def _add_metrics(command: argparse.ArgumentParser) -> None:
    for field in sorted(METRIC_FIELDS):
        command.add_argument("--" + field.replace("_", "-"), type=int)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="structured end-to-end digest timing log")
    sub = result.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="create one run_id for one digest input")
    _add_kb(start)
    start.add_argument(
        "--source-type", default="unknown", choices=sorted(SOURCE_TYPES)
    )
    start.add_argument(
        "--source-ref",
        default="",
        help="optional source identity; only its SHA-256 hash is persisted",
    )

    stage = sub.add_parser("stage", help="record a stage start, completion, or failure")
    _add_kb(stage)
    stage.add_argument("--run-id", required=True)
    stage.add_argument("--stage", required=True, choices=sorted(STAGE_ACTIONS))
    stage.add_argument("--status", required=True, choices=sorted(STAGE_STATUSES))
    stage.add_argument("--detail-code", default="")
    stage.add_argument(
        "--source-type",
        default="",
        choices=sorted(SOURCE_TYPES - {"unknown"}),
        help="set the resolved type when the classify stage completes",
    )
    _add_metrics(stage)

    complete = sub.add_parser("complete", help="finish a digest run")
    _add_kb(complete)
    complete.add_argument("--run-id", required=True)
    complete.add_argument("--status", required=True, choices=sorted(RUN_STATUSES))
    complete.add_argument("--error-code", default="")
    _add_metrics(complete)

    usage = sub.add_parser("usage", help="record one privacy-safe model usage receipt")
    _add_kb(usage)
    usage.add_argument("--run-id", required=True)
    usage.add_argument("--stage", required=True, choices=sorted(STAGE_ACTIONS))
    usage.add_argument("--worker-role", required=True, choices=sorted(WORKER_ROLES))
    usage.add_argument("--usage-source", required=True, choices=sorted(USAGE_SOURCES))
    usage.add_argument("--call-id", required=True)
    for field in sorted(USAGE_FIELDS):
        usage.add_argument("--" + field.replace("_", "-"), type=int, required=True)

    waiting = sub.add_parser("wait", help="pause active timing while waiting for user")
    _add_kb(waiting)
    waiting.add_argument("--run-id", required=True)
    waiting.add_argument("--reason-code", required=True, choices=sorted(WAIT_REASONS))

    resume = sub.add_parser("resume", help="resume a waiting or stale digest run")
    _add_kb(resume)
    resume.add_argument("--run-id", required=True)

    heartbeat = sub.add_parser("heartbeat", help="refresh one open long-running stage")
    _add_kb(heartbeat)
    heartbeat.add_argument("--run-id", required=True)
    heartbeat.add_argument("--stage", required=True, choices=sorted(STAGE_ACTIONS))

    listing = sub.add_parser("list", help="list recent digest runs and slowest stages")
    _add_kb(listing)
    listing.add_argument("--limit", type=int, default=20)

    show = sub.add_parser("show", help="show the complete timeline for one digest run")
    _add_kb(show)
    show.add_argument("--run-id", required=True)
    return result


def _metrics(args: argparse.Namespace) -> dict[str, int]:
    return {
        field: value
        for field in METRIC_FIELDS
        if (value := getattr(args, field, None)) is not None
    }


def main() -> int:
    args = parser().parse_args()
    if not args.kb:
        error = DigestRunError(
            "KB_CONFIG_MISSING",
            "No --kb was provided and .kbconfig does not exist.",
        )
        print(json.dumps({"status": "error", "error": error.as_dict()}))
        return 2
    kb = Path(args.kb)
    try:
        if args.command == "start":
            output = start_run(
                kb,
                source_type=args.source_type,
                source_ref=args.source_ref,
            )
        elif args.command == "stage":
            output = record_stage(
                kb,
                run_id=args.run_id,
                stage=args.stage,
                status=args.status,
                detail_code=args.detail_code,
                source_type=args.source_type,
                metrics=_metrics(args),
            )
        elif args.command == "complete":
            output = finish_run(
                kb,
                run_id=args.run_id,
                status=args.status,
                error_code=args.error_code,
                metrics=_metrics(args),
            )
        elif args.command == "usage":
            output = record_usage(
                kb,
                run_id=args.run_id,
                stage=args.stage,
                worker_role=args.worker_role,
                usage_source=args.usage_source,
                call_id=args.call_id,
                usage={field: getattr(args, field) for field in USAGE_FIELDS},
            )
        elif args.command == "wait":
            output = wait_for_user(
                kb,
                run_id=args.run_id,
                reason_code=args.reason_code,
            )
        elif args.command == "resume":
            output = resume_run(kb, run_id=args.run_id)
        elif args.command == "heartbeat":
            output = record_heartbeat(
                kb,
                run_id=args.run_id,
                stage=args.stage,
            )
        elif args.command == "list":
            output = list_runs(kb, limit=args.limit)
        else:
            output = show_run(kb, run_id=args.run_id)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except DigestRunError as exc:
        print(
            json.dumps(
                {"status": "error", "error": exc.as_dict()},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    except Exception as exc:
        error = DigestRunError("DIGEST_RUN_ERROR", f"unexpected: {exc}")
        print(
            json.dumps(
                {"status": "error", "error": error.as_dict()},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
