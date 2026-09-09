#!/usr/bin/env python3
"""Manage opt-in Dreaming scheduling without changing existing skill commands."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from dreaming_cli_action import register_commands as register_action_commands  # noqa: E402
from dreaming_cli_process import register_commands as register_process_commands  # noqa: E402
from dreaming_cli_report import register_commands as register_report_commands  # noqa: E402
from dreaming_cli_review import register_commands as register_review_commands  # noqa: E402
from dreaming_cli_schedule import register_commands as register_schedule_commands  # noqa: E402
from dreaming_scheduler import DreamingError  # noqa: E402


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--pretty", action="store_true")
    sub = result.add_subparsers(dest="operation", required=True)
    register_schedule_commands(sub)
    register_process_commands(sub)
    register_review_commands(sub)
    register_report_commands(sub)
    register_action_commands(sub)
    return result


def _validate_kb(value: Path) -> Path:
    kb = value.expanduser().resolve()
    if not kb.is_dir():
        raise DreamingError("DREAMING_KB_INVALID", f"知识库目录不存在: {kb}")
    if kb == ROOT or ROOT in kb.parents:
        raise DreamingError("DREAMING_KB_INVALID", "Dreaming 状态不得写入 byteworker skill 仓库。")
    return kb


def _run(args: argparse.Namespace) -> object:
    return args._handler(args, _validate_kb(args.kb), ROOT)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        value = _run(args)
        json.dump(value, sys.stdout, ensure_ascii=False, sort_keys=True, indent=2 if args.pretty else None)
        sys.stdout.write("\n")
        return 0
    except DreamingError as exc:
        json.dump(
            {"error": exc.as_dict()},
            sys.stdout,
            ensure_ascii=False,
            sort_keys=True,
            indent=2 if args.pretty else None,
        )
        sys.stdout.write("\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
