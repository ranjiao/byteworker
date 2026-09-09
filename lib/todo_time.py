"""Deterministic Todo preference and natural-language time parsing."""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from todo_models import Preferences


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


def local_now(value: str | None, prefs: Preferences) -> datetime:
    timezone = ZoneInfo(prefs.timezone)
    if not value:
        return datetime.now(timezone)
    parsed = datetime.fromisoformat(value.replace(" ", "T"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone)
    return parsed.astimezone(timezone)


def cn_number(value: str) -> int:
    if value.isdigit():
        return int(value)
    values = {
        "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3,
        "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
    }
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


def extract_clock(expression: str, default: time) -> tuple[time, bool]:
    colon = re.search(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)", expression)
    explicit = False
    if colon:
        hour, minute = int(colon.group(1)), int(colon.group(2))
        explicit = True
    else:
        point = re.search(
            r"([零〇一二两三四五六七八九十\d]{1,3})\s*[点時时]"
            r"(?:\s*(半)|\s*([零〇一二两三四五六七八九十\d]{1,2})\s*分?)?",
            expression,
        )
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
        "今早": "今天早上", "今晚": "今天晚上",
        "明早": "明天早上", "明晚": "明天晚上",
    }.items():
        expression = expression.replace(source, target_value)
    timezone = ZoneInfo(prefs.timezone)
    default = prefs.default_remind_time if kind == "remind" else prefs.default_due_time
    clock, explicit_clock = extract_clock(expression, default)

    iso_match = re.search(r"(\d{4}-\d{2}-\d{2})(?:[T\s](\d{1,2}:\d{2})(?::\d{2})?)?", expression)
    target: date | None = None
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

    result = datetime.combine(target, clock, tzinfo=timezone)
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


def parse_iso(value: str, timezone) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone)
