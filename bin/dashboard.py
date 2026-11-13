#!/usr/bin/env python3
"""Read dashboard settings or generate the current dashboard."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from dashboard_settings import DashboardError  # noqa: E402
from dashboard_view import dashboard_inputs, dashboard_view  # noqa: E402


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("command", choices=("view", "inputs"))
    result.add_argument("--kb", type=Path, required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = (dashboard_view if args.command == "view" else dashboard_inputs)(args.kb)
    except (OSError, ValueError) as exc:
        code = exc.code if isinstance(exc, DashboardError) else "DASHBOARD_READ_FAILED"
        print(json.dumps({"error": {"code": code, "message": str(exc)}}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
