"""Application operations for Todo commands."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from todo_models import Preferences, Todo
from todo_store import ensure_initialized, get_todo, load_todos, next_id, save, todo_json
from todo_time import iso, parse_iso, resolve_time


def command_check(todos: list[Todo], now: datetime, window_hours: int) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    for todo in todos:
        if todo.status not in {"open", "waiting"}:
            continue
        snoozed = parse_iso(todo.fields.get("snoozed_until", ""), now.tzinfo)
        if snoozed and now < snoozed:
            continue
        reminded = parse_iso(todo.fields.get("last_reminded_at", ""), now.tzinfo)
        remind_at = parse_iso(todo.fields.get("remind_at", ""), now.tzinfo)
        due_at = parse_iso(todo.fields.get("due_at", ""), now.tzinfo)
        category = ""
        if remind_at and remind_at <= now and (not reminded or reminded < remind_at):
            category = "reminder"
        elif due_at and due_at < now and (not reminded or reminded.astimezone(now.tzinfo).date() < now.date()):
            category = "overdue"
        elif due_at and now <= due_at <= now + timedelta(hours=window_hours) and (
            not reminded or reminded.astimezone(now.tzinfo).date() < now.date()
        ):
            category = "due_soon"
        if category:
            results.append({"category": category, **todo_json(todo)})
    priority = {"reminder": 0, "overdue": 1, "due_soon": 2}
    return sorted(
        results,
        key=lambda item: (priority[item["category"]], item.get("due_at") or item.get("remind_at") or ""),
    )


def execute_storage_command(args, kb_dir: Path, prefs: Preferences, now: datetime):
    existed = (kb_dir / "todo.md").exists()
    path = ensure_initialized(kb_dir, getattr(args, "template", None) if args.command == "init" else None)
    preamble, todos = load_todos(path)
    if args.command == "init":
        return {"path": str(path), "created": not existed}
    if args.command == "add":
        title = args.title.strip()
        if not title:
            raise ValueError("Todo 标题不能为空")
        todo_id = next_id(todos, now)
        due_at = iso(resolve_time(args.due, now, prefs, "due")) if args.due else ""
        remind_at = iso(resolve_time(args.remind, now, prefs, "remind")) if args.remind else ""
        expressions = [value for value in (args.due, args.remind) if value]
        fields = {
            "kind": args.kind,
            "status": "open",
            "created_at": iso(now),
            "updated_at": iso(now),
            "due_at": due_at,
            "remind_at": remind_at,
            "time_expression": " / ".join(expressions),
            "snoozed_until": "",
            "source": args.source,
            "links": ", ".join(dict.fromkeys(args.link)),
            "reason": args.reason,
            "last_reminded_at": "",
            "note": args.note,
        }
        todo = Todo(todo_id, title, fields)
        todos.append(todo)
        save(path, preamble, todos)
        return todo_json(todo)
    if args.command == "list":
        return [
            todo_json(todo)
            for todo in todos
            if args.scope == "all"
            or (args.scope == "active") == (todo.status in {"open", "waiting"})
        ]
    if args.command == "check":
        return command_check(todos, now, args.window_hours or prefs.due_soon_hours)
    if args.command == "status":
        todo = get_todo(todos, args.todo_id)
        todo.fields["status"] = args.value
        todo.fields["updated_at"] = iso(now)
        save(path, preamble, todos)
        return todo_json(todo)
    if args.command == "snooze":
        todo = get_todo(todos, args.todo_id)
        todo.fields["snoozed_until"] = iso(resolve_time(args.until, now, prefs, "remind"))
        todo.fields["updated_at"] = iso(now)
        save(path, preamble, todos)
        return todo_json(todo)
    if args.command == "mark-reminded":
        todo = get_todo(todos, args.todo_id)
        todo.fields["last_reminded_at"] = iso(now)
        todo.fields["updated_at"] = iso(now)
        save(path, preamble, todos)
        return todo_json(todo)
    if args.command == "edit":
        todo = get_todo(todos, args.todo_id)
        if args.title:
            todo.title = args.title.strip()
        if args.clear_due:
            todo.fields["due_at"] = ""
        elif args.due:
            todo.fields["due_at"] = iso(resolve_time(args.due, now, prefs, "due"))
            todo.fields["time_expression"] = args.due
        if args.clear_remind:
            todo.fields["remind_at"] = ""
        elif args.remind:
            todo.fields["remind_at"] = iso(resolve_time(args.remind, now, prefs, "remind"))
            todo.fields["time_expression"] = args.remind
        if args.note is not None:
            todo.fields["note"] = args.note
        todo.fields["updated_at"] = iso(now)
        save(path, preamble, todos)
        return todo_json(todo)
    raise AssertionError(args.command)
