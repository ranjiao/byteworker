#!/usr/bin/env python3
"""CLI for Byteworker's one-shot, quiet session preflight."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from session_preflight import run_preflight  # noqa: E402


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Run Byteworker session preflight")
    result.add_argument("--kb", type=Path)
    result.add_argument(
        "--require",
        action="append",
        choices=("feishu", "meego"),
        default=[],
        help="强制检查本次来源 runtime；可重复",
    )
    result.add_argument("--skip-update", action="store_true", help=argparse.SUPPRESS)
    result.add_argument("--json", action="store_true", help="健康时也输出完整 JSON")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    result = run_preflight(
        ROOT,
        kb_override=args.kb,
        required_sources=args.require,
        skip_update=args.skip_update,
        environ=os.environ,
    )
    if args.json or result["status"] != "healthy":
        payload = result
        if not args.json:
            payload = {
                key: result[key]
                for key in ("schema_version", "status", "ready", "kb", "notices")
            }
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
