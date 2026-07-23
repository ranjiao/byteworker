#!/usr/bin/env python3
"""Deterministic storage and reminder checks for byteworker todo.md."""

from __future__ import annotations

import argparse
import calendar
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


HEADING_RE = re.compile(r"^### \[([ xX])\] (T-\d{8}-\d{3}) · (.+)$")
FIELD_RE = re.compile(r"^- ([a-z_]+):\s*(.*)$")
VALID_STATUS = {"open", "waiting", "done", "cancelled"}
VALID_KIND = {"task", "follow_up", "watch"}
FIELD_ORDER = (
    "kind",
    "status",
    "created_at",
    "updated_at",
    "due_at",
    "remind_at",
    "time_expression",
    "snoozed_until",
    "source",
    "links",
    "reason",
    "last_reminded_at",
    "note",
)
DEFAULT_PREAMBLE = """# TODO

<!-- 这是 byteworker 维护的个人待办真相源。
     日常请直接对 agent 说自然语言；T-YYYYMMDD-NNN 只用于内部去重、关联和审计。 -->
"""


@dataclass
class Preferences:
    timezone: str = "Asia/Shanghai"
    default_remind_time: time = time(9, 0)
    default_due_time: time = time(18, 0)
    due_soon_hours: int = 24


@dataclass
class Todo:
    todo_id: str
    title: str
    fields: Dict[str, str] = field(default_factory=dict)

    @property
    def status(self) -> str:
        return self.fields.get("status", "open")


def parse_clock(value: str, fallback: time) -> time:
    match = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", value)
    return time(int(match.group(1)), int(match.group(2))) if match else fallback


def load_preferences(kb_dir: Path) -> Preferences:
    prefs = Preferences()
    path = kb_dir / "context.md"
    if not path.exists():
        return prefs
    text_value = path.read_text(encoding="utf-8")
    timezone_match = re.search(r"^\|\s*时区\s*\|\s*([^|]+?)\s*\|\s*$", text_value, re.M)
    if timezone_match:
        candidate = timezone_match.group(1).strip()
        try:
            ZoneInfo(candidate)
            prefs.timezone = candidate
        except ZoneInfoNotFoundError:
            pass
    for line in text_value.splitlines():
        if "未指定具体时间的提醒" in line:
            prefs.default_remind_time = parse_clock(line, prefs.default_remind_time)
        elif "只说截止日期" in line or "未指定具体时间的截止" in line:
            prefs.default_due_time = parse_clock(line, prefs.default_due_time)
        elif "临近到期窗口" in line:
            match = re.search(r"(\d+)\s*小时", line)
            if match:
                prefs.due_soon_hours = int(match.group(1))
    return prefs


def local_now(value: Optional[str], prefs: Preferences) -> datetime:
    tz = ZoneInfo(prefs.timezone)
    if not value:
        return datetime.now(tz)
    parsed = datetime.fromisoformat(value.replace(" ", "T"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def cn_number(value: str) -> int:
    if value.isdigit():
        return int(value)
    values = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if value == "十":
        return 10
    if "十" in value:
        left, right = value.split("十", 1)
        tens = values.get(left, 1) if left else 1
        ones = values.get(right, 0) if right else 0
        return tens * 10 + ones
    if value in values:
        return values[value]
    raise ValueError(f"无法解析数字：{value}")


def extract_clock(expression: str, default: time) -> Tuple[time, bool]:
    colon = re.search(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)", expression)
    explicit = False
    if colon:
        hour, minute = int(colon.group(1)), int(colon.group(2))
        explicit = True
    else:
        point = re.search(r"([零〇一二两三四五六七八九十\d]{1,3})\s*[点時时](?:\s*(半)|\s*([零〇一二两三四五六七八九十\d]{1,2})\s*分?)?", expression)
        if point:
            hour = cn_number(point.group(1))
            minute = 30 if point.group(2) else cn_number(point.group(3)) if point.group(3) else 0
            explicit = True
        elif "下班前" in expression:
            hour, minute = default.hour, default.minute
        elif "下午" in expression:
            hour, minute = 15, 0
        elif "晚上" in expression:
            hour, minute = 20, 0
        elif "中午" in expression:
            hour, minute = 12, 0
        elif "上午" in expression or "早上" in expression:
            hour, minute = 9, 0
        else:
            hour, minute = default.hour, default.minute

    if explicit:
        if any(word in expression for word in ("下午", "晚上")) and hour < 12:
            hour += 12
        elif "中午" in expression and hour < 11:
            hour += 12
        elif "凌晨" in expression and hour == 12:
            hour = 0
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"无法解析时间：{expression}")
    return time(hour, minute), explicit


def resolve_time(expression: str, now: datetime, prefs: Preferences, kind: str) -> datetime:
    expression = expression.strip()
    if not expression:
        raise ValueError("时间表达不能为空")
    for source, target_value in {
        "今早": "今天早上",
        "今晚": "今天晚上",
        "明早": "明天早上",
        "明晚": "明天晚上",
    }.items():
        expression = expression.replace(source, target_value)
    tz = ZoneInfo(prefs.timezone)
    default = prefs.default_remind_time if kind == "remind" else prefs.default_due_time
    clock, explicit_clock = extract_clock(expression, default)

    iso_match = re.search(r"(\d{4}-\d{2}-\d{2})(?:[T\s](\d{1,2}:\d{2})(?::\d{2})?)?", expression)
    target: Optional[date] = None
    if iso_match:
        target = date.fromisoformat(iso_match.group(1))
        if iso_match.group(2):
            hour, minute = map(int, iso_match.group(2).split(":"))
            clock = time(hour, minute)
            explicit_clock = True
    else:
        md_match = re.search(r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})[日号]?", expression)
        if md_match:
            year = int(md_match.group(1) or now.year)
            target = date(year, int(md_match.group(2)), int(md_match.group(3)))
            if not md_match.group(1) and target < now.date():
                target = target.replace(year=year + 1)

    if target is None:
        days_match = re.search(r"([零〇一二两三四五六七八九十\d]+)\s*天后", expression)
        if "大后天" in expression:
            target = now.date() + timedelta(days=3)
        elif "后天" in expression:
            target = now.date() + timedelta(days=2)
        elif "明天" in expression:
            target = now.date() + timedelta(days=1)
        elif "今天" in expression:
            target = now.date()
        elif days_match:
            target = now.date() + timedelta(days=cn_number(days_match.group(1)))
        elif "月底" in expression:
            target = date(now.year, now.month, calendar.monthrange(now.year, now.month)[1])
        else:
            weekday_match = re.search(r"(?:周|星期)([一二三四五六日天])", expression)
            weekday_map = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
            if weekday_match:
                wanted = weekday_map[weekday_match.group(1)]
                if "下周" in expression:
                    monday = now.date() - timedelta(days=now.weekday()) + timedelta(days=7)
                    target = monday + timedelta(days=wanted)
                elif "本周" in expression:
                    monday = now.date() - timedelta(days=now.weekday())
                    target = monday + timedelta(days=wanted)
                else:
                    delta = (wanted - now.weekday()) % 7
                    target = now.date() + timedelta(days=delta)
            elif "下周" in expression:
                target = now.date() - timedelta(days=now.weekday()) + timedelta(days=7)

    if target is None and explicit_clock:
        target = now.date()
    if target is None:
        raise ValueError(f"无法解析时间表达：{expression}")

    result = datetime.combine(target, clock, tzinfo=tz)
    weekday_without_scope = re.search(r"(?:周|星期)[一二三四五六日天]", expression) and not any(
        word in expression for word in ("本周", "下周")
    )
    time_only = target == now.date() and explicit_clock and not re.search(
        r"今天|明天|后天|天后|周|星期|月底|\d{4}-\d{2}-\d{2}|\d{1,2}月\d{1,2}", expression
    )
    if result <= now and (weekday_without_scope or time_only):
        result += timedelta(days=7 if weekday_without_scope else 1)
    return result


def iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def parse_iso(value: str, timezone) -> Optional[datetime]:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone)


def load_todos(path: Path) -> Tuple[str, List[Todo]]:
    if not path.exists():
        return DEFAULT_PREAMBLE, []
    lines = path.read_text(encoding="utf-8").splitlines()
    active_index = next((i for i, line in enumerate(lines) if line == "## Active"), len(lines))
    if active_index == len(lines) or "## Completed" not in lines:
        raise ValueError("todo.md 缺少固定的 Active / Completed 章节，拒绝重写")
    preamble = "\n".join(lines[:active_index]).rstrip() + "\n"
    todos: List[Todo] = []
    current: Optional[Todo] = None
    in_comment = False
    for line in lines[active_index:]:
        if "<!--" in line:
            in_comment = True
        if in_comment:
            if "-->" in line:
                in_comment = False
            continue
        heading = HEADING_RE.match(line)
        if heading:
            current = Todo(heading.group(2), heading.group(3).strip(), {})
            todos.append(current)
            continue
        if current:
            field_match = FIELD_RE.match(line)
            if field_match:
                current.fields[field_match.group(1)] = field_match.group(2).strip()
    ids = [todo.todo_id for todo in todos]
    if len(ids) != len(set(ids)):
        raise ValueError("todo.md 中存在重复 id，拒绝重写")
    for todo in todos:
        if todo.status not in VALID_STATUS:
            raise ValueError(f"{todo.todo_id} 的 status 非法：{todo.status}")
        kind = todo.fields.get("kind", "task")
        if kind not in VALID_KIND:
            raise ValueError(f"{todo.todo_id} 的 kind 非法：{kind}")
    return preamble, todos


def next_sort_time(todo: Todo) -> str:
    values = [todo.fields.get(key, "") for key in ("snoozed_until", "remind_at", "due_at")]
    return min((value for value in values if value), default="9999")


def render_todos(preamble: str, todos: Iterable[Todo]) -> str:
    todos_list = list(todos)
    active = sorted((todo for todo in todos_list if todo.status in {"open", "waiting"}), key=next_sort_time)
    completed = sorted(
        (todo for todo in todos_list if todo.status in {"done", "cancelled"}),
        key=lambda todo: todo.fields.get("updated_at", ""),
        reverse=True,
    )

    def render_item(todo: Todo) -> List[str]:
        checkbox = "x" if todo.status in {"done", "cancelled"} else " "
        result = [f"### [{checkbox}] {todo.todo_id} · {todo.title}"]
        seen = set()
        for key in FIELD_ORDER:
            result.append(f"- {key}: {todo.fields.get(key, '')}")
            seen.add(key)
        for key in sorted(set(todo.fields) - seen):
            result.append(f"- {key}: {todo.fields[key]}")
        return result

    output = [preamble.rstrip(), "", "## Active", ""]
    for todo in active:
        output.extend(render_item(todo))
        output.append("")
    output.extend(["## Completed", ""])
    for todo in completed:
        output.extend(render_item(todo))
        output.append("")
    return "\n".join(output).rstrip() + "\n"


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def ensure_initialized(kb_dir: Path, template: Optional[Path]) -> Path:
    path = kb_dir / "todo.md"
    if not path.exists():
        content = template.read_text(encoding="utf-8") if template else render_todos(DEFAULT_PREAMBLE, [])
        atomic_write(path, content)
    return path


def next_id(todos: Iterable[Todo], now: datetime) -> str:
    prefix = f"T-{now:%Y%m%d}-"
    numbers = [int(todo.todo_id.rsplit("-", 1)[1]) for todo in todos if todo.todo_id.startswith(prefix)]
    next_number = max(numbers, default=0) + 1
    if next_number > 999:
        raise ValueError("当天 Todo 已达到 999 条，无法继续分配 id")
    return f"{prefix}{next_number:03d}"


def get_todo(todos: Iterable[Todo], todo_id: str) -> Todo:
    for todo in todos:
        if todo.todo_id == todo_id:
            return todo
    raise ValueError(f"未找到 todo：{todo_id}")


def todo_json(todo: Todo) -> Dict[str, str]:
    return {"id": todo.todo_id, "title": todo.title, **todo.fields}


def save(path: Path, preamble: str, todos: List[Todo]) -> None:
    atomic_write(path, render_todos(preamble, todos))


def command_check(todos: List[Todo], now: datetime, window_hours: int) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []
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
    return sorted(results, key=lambda item: (priority[item["category"]], item.get("due_at") or item.get("remind_at") or ""))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage byteworker todo.md")
    parser.add_argument("kb_dir", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init")
    init.add_argument("--template", type=Path)

    parse = sub.add_parser("parse-time")
    parse.add_argument("expression")
    parse.add_argument("--kind", choices=("remind", "due"), default="remind")
    parse.add_argument("--now")

    add = sub.add_parser("add")
    add.add_argument("--title", required=True)
    add.add_argument("--kind", choices=sorted(VALID_KIND), default="task")
    add.add_argument("--due")
    add.add_argument("--remind")
    add.add_argument("--source", default="direct:user")
    add.add_argument("--link", action="append", default=[])
    add.add_argument("--reason", default="")
    add.add_argument("--note", default="")
    add.add_argument("--now")

    listing = sub.add_parser("list")
    listing.add_argument("--scope", choices=("active", "completed", "all"), default="active")

    check = sub.add_parser("check")
    check.add_argument("--now")
    check.add_argument("--window-hours", type=int)

    status = sub.add_parser("status")
    status.add_argument("todo_id")
    status.add_argument("value", choices=sorted(VALID_STATUS))
    status.add_argument("--now")

    snooze = sub.add_parser("snooze")
    snooze.add_argument("todo_id")
    snooze.add_argument("until")
    snooze.add_argument("--now")

    reminded = sub.add_parser("mark-reminded")
    reminded.add_argument("todo_id")
    reminded.add_argument("--now")

    edit = sub.add_parser("edit")
    edit.add_argument("todo_id")
    edit.add_argument("--title")
    edit.add_argument("--due")
    edit.add_argument("--remind")
    edit.add_argument("--clear-due", action="store_true")
    edit.add_argument("--clear-remind", action="store_true")
    edit.add_argument("--note")
    edit.add_argument("--now")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
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

    existed = (kb_dir / "todo.md").exists()
    path = ensure_initialized(kb_dir, getattr(args, "template", None) if args.command == "init" else None)
    try:
        preamble, todos = load_todos(path)
    except ValueError as error:
        raise SystemExit(f"错误：{error}") from error

    try:
        if args.command == "init":
            result = {"path": str(path), "created": not existed}
        elif args.command == "add":
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
            result = todo_json(todo)
        elif args.command == "list":
            selected = [todo for todo in todos if args.scope == "all" or (args.scope == "active") == (todo.status in {"open", "waiting"})]
            result = [todo_json(todo) for todo in selected]
        elif args.command == "check":
            result = command_check(todos, now, args.window_hours or prefs.due_soon_hours)
        elif args.command == "status":
            todo = get_todo(todos, args.todo_id)
            todo.fields["status"] = args.value
            todo.fields["updated_at"] = iso(now)
            save(path, preamble, todos)
            result = todo_json(todo)
        elif args.command == "snooze":
            todo = get_todo(todos, args.todo_id)
            todo.fields["snoozed_until"] = iso(resolve_time(args.until, now, prefs, "remind"))
            todo.fields["updated_at"] = iso(now)
            save(path, preamble, todos)
            result = todo_json(todo)
        elif args.command == "mark-reminded":
            todo = get_todo(todos, args.todo_id)
            todo.fields["last_reminded_at"] = iso(now)
            todo.fields["updated_at"] = iso(now)
            save(path, preamble, todos)
            result = todo_json(todo)
        elif args.command == "edit":
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
            result = todo_json(todo)
        else:
            raise AssertionError(args.command)
    except ValueError as error:
        raise SystemExit(f"错误：{error}") from error

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
