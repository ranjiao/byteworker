"""The persisted inputs of the dashboard and the legacy snapshot reader."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path


SETTINGS_PATH = "settings.md"
LEGACY_PATH = "dashboard.md"
WATCH_HEADING = "长期关注"
REMINDER_HEADING = "看板提醒"
WATCH_COLUMNS = ("关注项", "绑定节点", "关注什么")
LEGACY_EMPTY_ALERTS = (
    "_暂无派生项:节点无 stale、无超过 90 天未验证的非 reading 节点、无未裁决冲突。"
    "说「提醒我关注 XX」可手动加。_"
)


class DashboardError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Watch:
    title: str
    node: str
    note: str

    @property
    def node_id(self) -> str:
        value = self.node.strip("`")
        return "" if value in {"—", "-", "无"} else value


@dataclass(frozen=True)
class DashboardSettings:
    watches: tuple[Watch, ...] = ()
    reminders: tuple[str, ...] = ()


def safe_path(kb: Path, relative: str) -> Path:
    path = kb / relative
    if path.resolve() != path:
        raise DashboardError("DASHBOARD_PATH_INVALID", f"看板输入不得经过符号链接: {relative}")
    return path


def _sections(text: str) -> tuple[str, dict[str, str]]:
    headings = list(re.finditer(r"(?m)^##[ \t]+(.+)$", text))
    sections: dict[str, str] = {}
    for index, heading in enumerate(headings):
        name = heading[1].strip()
        if name in sections:
            raise DashboardError("DASHBOARD_SETTINGS_INVALID", "看板输入包含重复章节")
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        sections[name] = text[heading.end():end].strip()
    return text[:headings[0].start()].strip() if headings else text.strip(), sections


def _table_rows(text: str, *, legacy: bool) -> tuple[Watch, ...]:
    rows = []
    width = 4 if legacy else 3
    expected = (*WATCH_COLUMNS, "当前状态") if legacy else WATCH_COLUMNS
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if not line.startswith("|") or not line.endswith("|"):
            raise DashboardError("DASHBOARD_SETTINGS_INVALID", "长期关注必须是 Markdown 表格")
        cells = tuple(cell.strip() for cell in re.split(r"(?<!\\)\|", line[1:-1]))
        if len(cells) != width:
            raise DashboardError("DASHBOARD_SETTINGS_INVALID", "长期关注表格列数不符")
        rows.append(cells)
    if not rows and legacy:
        return ()
    if len(rows) < 2 or rows[0] != expected or not all(
        re.fullmatch(r":?-{3,}:?", cell) for cell in rows[1]
    ):
        raise DashboardError("DASHBOARD_SETTINGS_INVALID", "长期关注表头不符")
    if any(not row[0] for row in rows[2:]):
        raise DashboardError("DASHBOARD_SETTINGS_INVALID", "长期关注的关注项不能为空")
    return tuple(Watch(*row[:3]) for row in rows[2:])


def _reminders(text: str) -> tuple[str, ...]:
    # Each item may have indented Markdown continuation lines.
    items: list[list[str]] = []
    for line in text.splitlines():
        if re.match(r"^[-*+]\s+\S", line):
            items.append([line])
        elif items and (not line.strip() or line.startswith(("  ", "\t"))):
            items[-1].append(line)
        elif line.strip():
            raise DashboardError("DASHBOARD_SETTINGS_INVALID", "看板提醒必须是 Markdown 列表")
    return tuple("\n".join(lines).rstrip() for lines in items)


def parse_settings(text: str) -> DashboardSettings:
    _, sections = _sections(text)
    if WATCH_HEADING not in sections or REMINDER_HEADING not in sections:
        raise DashboardError("DASHBOARD_SETTINGS_INVALID", "settings.md 缺少长期关注或看板提醒章节")
    return DashboardSettings(
        _table_rows(sections[WATCH_HEADING], legacy=False),
        _reminders(sections[REMINDER_HEADING]),
    )


def parse_legacy(text: str) -> DashboardSettings:
    preamble, original_sections = _sections(text)
    sections = {re.sub(r"^(?:📌|⚠️?|📅)\s*", "", name): body for name, body in original_sections.items()}
    unknown = set(sections) - {WATCH_HEADING, "需要关注"}
    if (
        WATCH_HEADING not in sections or "需要关注" not in sections
        or len(sections) != len(original_sections)
        or len(unknown) > 1
        or any(not re.fullmatch(r"今日进展\s*[(（]\d{4}-\d{2}-\d{2}[)）]", name) for name in unknown)
        or any(not re.fullmatch(r"(?:#\s+工作看板(?:\s*·\s*dashboard)?|>\s*最后刷新[:：].+)", line)
               for line in preamble.splitlines() if line.strip())
    ):
        raise DashboardError("DASHBOARD_LEGACY_AMBIGUOUS", "旧看板结构有无法确认用途的内容")
    alerts = sections["需要关注"]
    if alerts == LEGACY_EMPTY_ALERTS:
        alerts = ""
    reminders = []
    for item in _reminders(alerts):
        label = re.match(r"^[-*+]\s+[（(](手动|派生)[)）]\s*\S", item)
        if not label:
            raise DashboardError("DASHBOARD_LEGACY_AMBIGUOUS", "旧看板有未标明手动或派生的提醒")
        if label[1] == "手动":
            reminders.append(re.sub(r"^([-*+]\s+)[（(]手动[)）]\s*", r"\1", item))
    return DashboardSettings(_table_rows(sections[WATCH_HEADING], legacy=True), tuple(reminders))


def settings_markdown(value: DashboardSettings) -> str:
    rows = [
        "# 知识库设置", "", f"## {WATCH_HEADING}", "",
        "| " + " | ".join(WATCH_COLUMNS) + " |", "| --- | --- | --- |",
        *("| " + " | ".join((watch.title, watch.node, watch.note)) + " |" for watch in value.watches),
        "", f"## {REMINDER_HEADING}", "",
        *value.reminders,
    ]
    return "\n".join(rows).rstrip() + "\n"


def load_dashboard_settings(kb: Path) -> tuple[DashboardSettings, str | None, str]:
    for relative, parser in ((SETTINGS_PATH, parse_settings), (LEGACY_PATH, parse_legacy)):
        path = safe_path(kb, relative)
        if path.exists():
            content = path.read_bytes()
            return parser(content.decode("utf-8")), relative, "sha256:" + hashlib.sha256(content).hexdigest()
    return DashboardSettings(), None, ""
