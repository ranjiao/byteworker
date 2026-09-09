"""Typed Todo state shared by parsing, storage, and command services."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time


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
    fields: dict[str, str] = field(default_factory=dict)

    @property
    def status(self) -> str:
        return self.fields.get("status", "open")
