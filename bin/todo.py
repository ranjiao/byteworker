#!/usr/bin/env python3
"""CLI adapter for deterministic Todo services."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from todo_models import Preferences, Todo, VALID_KIND, VALID_STATUS  # noqa: E402,F401
from todo_service import command_check, execute_storage_command  # noqa: E402,F401
from todo_store import (  # noqa: E402,F401
    TodoTransactionError,
    ensure_initialized,
    execute_write_transaction,
    load_todos,
    save,
)
from todo_time import iso, load_preferences, local_now, resolve_time  # noqa: E402,F401


WRITE_COMMANDS = {"init", "add", "status", "snooze", "mark-reminded", "edit"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage byteworker todo.md")
    parser.add_argument("kb_dir", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Initialize todo.md")
    init.add_argument("--template", type=Path)

    parse = sub.add_parser("parse-time", help="Resolve a supported time expression")
    parse.add_argument("expression")
    parse.add_argument("--kind", choices=("remind", "due"), default="remind")
    parse.add_argument("--now")

    add = sub.add_parser("add", help="Create a Todo")
    add.add_argument("--title", required=True)
    add.add_argument("--kind", choices=sorted(VALID_KIND), default="task")
    add.add_argument("--due")
    add.add_argument("--remind")
    add.add_argument("--source", default="direct:user")
    add.add_argument("--link", action="append", default=[])
    add.add_argument("--reason", default="")
    add.add_argument("--note", default="")
    add.add_argument("--now")

    listing = sub.add_parser("list", help="List Todos by lifecycle scope")
    listing.add_argument("--scope", choices=("active", "completed", "all"), default="active")

    check = sub.add_parser("check", help="Return reminders and due Todos")
    check.add_argument("--now")
    check.add_argument("--window-hours", type=int)

    status = sub.add_parser("status", help="Change Todo status")
    status.add_argument("todo_id")
    status.add_argument("value", choices=sorted(VALID_STATUS))
    status.add_argument("--now")

    snooze = sub.add_parser("snooze", help="Delay Todo reminders")
    snooze.add_argument("todo_id")
    snooze.add_argument("until")
    snooze.add_argument("--now")

    reminded = sub.add_parser("mark-reminded", help="Record reminder delivery")
    reminded.add_argument("todo_id")
    reminded.add_argument("--now")

    edit = sub.add_parser("edit", help="Edit mutable Todo fields")
    edit.add_argument("todo_id")
    edit.add_argument("--title")
    edit.add_argument("--due")
    edit.add_argument("--remind")
    edit.add_argument("--clear-due", action="store_true")
    edit.add_argument("--clear-remind", action="store_true")
    edit.add_argument("--note")
    edit.add_argument("--now")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    kb_dir: Path = args.kb_dir.expanduser().resolve()
    prefs = load_preferences(kb_dir)
    now = local_now(getattr(args, "now", None), prefs)
    if args.command == "parse-time":
        try:
            result = {
                "expression": args.expression,
                "resolved": iso(resolve_time(args.expression, now, prefs, args.kind)),
                "timezone": prefs.timezone,
            }
        except ValueError as error:
            raise SystemExit(f"错误：{error}") from error
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    try:
        if args.command in WRITE_COMMANDS:
            result = execute_write_transaction(
                kb_dir,
                now=now,
                command=args.command,
                operation=lambda: execute_storage_command(args, kb_dir, prefs, now),
            )
        else:
            result = execute_storage_command(args, kb_dir, prefs, now)
    except (OSError, ValueError, TodoTransactionError) as error:
        raise SystemExit(f"错误：{error}") from error

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
