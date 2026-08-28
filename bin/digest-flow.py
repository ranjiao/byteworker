#!/usr/bin/env python3
"""CLI for deterministic digest lifecycle orchestration."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from digest_flow import (  # noqa: E402
    DigestFlowError,
    capture_flow,
    commit_flow,
    flow_status,
    prepare_flow,
    start_flow,
)
from digest_run_log import SOURCE_TYPES  # noqa: E402


def configured_kb() -> str:
    config = ROOT / ".kbconfig"
    if not config.is_file():
        return ""
    return config.read_text(encoding="utf-8").splitlines()[0].strip()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="deterministic digest lifecycle orchestrator")
    sub = result.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start")
    start.add_argument("--kb", default=configured_kb())
    start.add_argument("--source-type", required=True, choices=sorted(SOURCE_TYPES - {"unknown"}))
    start.add_argument("--source-ref", default="")
    for name in ("capture", "prepare", "commit", "status"):
        command = sub.add_parser(name)
        command.add_argument("--kb", default=configured_kb())
        command.add_argument("--run-id", required=True)
        if name == "capture":
            command.add_argument("--request", required=True, type=Path)
        elif name == "prepare":
            command.add_argument("--bundle-request", required=True, type=Path)
        elif name == "commit":
            command.add_argument("--plan", required=True, type=Path)
    return result


def main() -> int:
    args = parser().parse_args()
    if not args.kb:
        print(json.dumps({"error": {"code": "KB_CONFIG_MISSING", "message": "未指定 KB。"}}))
        return 2
    kb = Path(args.kb)
    try:
        if args.command == "start":
            result = start_flow(kb, source_type=args.source_type, source_ref=args.source_ref)
        elif args.command == "capture":
            result = capture_flow(kb, run_id=args.run_id, request_path=args.request)
        elif args.command == "prepare":
            result = prepare_flow(kb, run_id=args.run_id, bundle_request=args.bundle_request)
        elif args.command == "commit":
            result = commit_flow(kb, run_id=args.run_id, plan_path=args.plan)
        else:
            result = flow_status(kb, run_id=args.run_id)
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    except DigestFlowError as exc:
        print(json.dumps({"error": exc.as_dict()}, ensure_ascii=False, separators=(",", ":")))
        return 2
    except Exception as exc:
        error = DigestFlowError("DIGEST_FLOW_ERROR", f"unexpected: {exc}")
        print(json.dumps({"error": error.as_dict()}, ensure_ascii=False, separators=(",", ":")))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
