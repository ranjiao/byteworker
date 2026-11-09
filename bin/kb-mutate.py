#!/usr/bin/env python3
"""Validate or execute deterministic non-digest KB mutations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from kb_mutation import (  # noqa: E402
    MutationError,
    execute_mutation,
    migrate_dashboard,
    validate_mutation,
    validation_report,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Apply a KB mutation plan")
    result.add_argument("command", choices=("validate", "execute", "migrate-dashboard"))
    result.add_argument("--kb", type=Path, required=True)
    result.add_argument("--plan", type=Path)
    result.add_argument("--apply", action="store_true", help="提交 migrate-dashboard，默认只校验")
    return result


def main(argv: list[str] | None = None) -> int:
    command_parser = parser()
    args = command_parser.parse_args(argv)
    if args.command == "migrate-dashboard":
        if args.plan is not None:
            command_parser.error("migrate-dashboard 不接受 --plan")
    elif args.plan is None or args.apply:
        command_parser.error("validate/execute 要求 --plan，且不接受 --apply")
    try:
        if args.command == "validate":
            output = validation_report(
                validate_mutation(args.kb, args.plan, ROOT)
            )
        elif args.command == "execute":
            output = execute_mutation(args.kb, args.plan, ROOT)
        else:
            output = migrate_dashboard(args.kb, ROOT, apply=args.apply)
    except MutationError as exc:
        print(
            json.dumps(
                {
                    "error": {
                        "code": exc.code,
                        "message": str(exc),
                    }
                },
                ensure_ascii=False,
            )
        )
        return 2
    print(json.dumps(output, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
