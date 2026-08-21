#!/usr/bin/env python3
"""CLI for bounded parallel digest source capture."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from digest_capture import DigestCaptureError, execute_capture_plan  # noqa: E402


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="execute bounded independent read-only digest capture jobs"
    )
    sub = result.add_subparsers(dest="command", required=True)
    execute = sub.add_parser("execute")
    execute.add_argument("--request", required=True, type=Path)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        receipt = execute_capture_plan(args.request)
    except DigestCaptureError as exc:
        json.dump({"error": exc.as_dict()}, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        return 1
    json.dump(receipt, sys.stdout, ensure_ascii=False, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
