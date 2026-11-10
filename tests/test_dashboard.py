from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from dashboard_settings import DashboardError, parse_legacy, parse_settings, settings_markdown
from dashboard_view import dashboard_inputs, dashboard_view


LEGACY = r"""# 工作看板 · dashboard
> 最后刷新:2026-06-03 11:41

## 📌 长期关注

| 关注项 | 绑定节点 | 关注什么 | 当前状态 |
| --- | --- | --- | --- |
| Project | `project-example` | exact \| note | obsolete snapshot |
| Loose | | plain note | — |

## ⚠️ 需要关注
- (派生) stale snapshot
- （手动） keep this reminder
  and its continuation

## 📅 今日进展(2026-06-03)
- 09:00 obsolete progress
"""


class DashboardTests(unittest.TestCase):
    def test_legacy_round_trip_only_keeps_manual_data(self):
        settings = parse_legacy(LEGACY)
        text = settings_markdown(settings)
        self.assertEqual(settings, parse_settings(text))
        self.assertEqual("project-example", settings.watches[0].node_id)
        self.assertEqual(r"exact \| note", settings.watches[0].note)
        self.assertEqual("", settings.watches[1].node_id)
        self.assertEqual(("- keep this reminder\n  and its continuation",), settings.reminders)
        self.assertNotIn("obsolete", text)
        self.assertNotIn("stale snapshot", text)
        with self.assertRaises(DashboardError):
            parse_legacy(LEGACY.replace("(派生) ", ""))

    def test_view_recomputes_local_day_node_status_and_deadlines_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            kb = Path(directory).resolve()
            legacy = kb / "dashboard.md"
            legacy.write_text(LEGACY, encoding="utf-8")
            self.assertEqual("dashboard.md", dashboard_inputs(kb)["source"])
            legacy.unlink()
            settings = kb / "settings.md"
            settings.write_text(settings_markdown(parse_legacy(LEGACY)), encoding="utf-8")
            (kb / "context.md").write_text("| 时区 | Asia/Shanghai |\n", encoding="utf-8")
            nodes = kb / "knowledge"
            nodes.mkdir()
            (nodes / "example.md").write_text(
                "---\nid: project-example\ntype: project\nstatus: current\nlast_verified: 2026-01-01\n"
                "sources:\n  - raw-example\n---\n> **TL;DR:** current node state [E1]\n\n"
                "## 证据\n"
                "| **[E1]** | `raw-example` | anchor_id=block | 2026-11-09 | 2026-11-09 | exact |\n"
                "[E1]: <https://example.test/source#block>\n",
                encoding="utf-8",
            )
            (nodes / "stale.md").write_text(
                "---\nid: project-unwatched\ntype: project\nstatus: stale\n---\n",
                encoding="utf-8",
            )
            raw = kb / "raw_data"
            raw.mkdir()
            (raw / "raw-example.md").write_text(
                "---\nraw_id: raw-example\nsource_title: Example\n"
                "source_url: https://example.test/source\ningested: 2026-11-09T01:00:00+08:00\n"
                "source_revision: abc\n---\n",
                encoding="utf-8",
            )
            (kb / "todo.md").write_text(
                "## Active\n### [ ] T-20261109-001 · Overdue\n- status: open\n"
                "- due_at: 2026-11-09T00:01:00+08:00\n"
                "- last_reminded_at: 2026-11-09T00:01:00+08:00\n"
                "### [ ] T-20261109-002 · Due soon\n- status: open\n"
                "- due_at: 2026-11-09T10:00:00+08:00\n## Completed\n",
                encoding="utf-8",
            )
            journal = kb / "journal" / "2026-11"
            journal.mkdir(parents=True)
            (journal / "2026-11-09.md").write_text("# 2026-11-09\n\n- 00:01 new progress\n", encoding="utf-8")
            before = {path: path.read_bytes() for path in kb.rglob("*.md")}
            first = dashboard_view(kb, at="2026-11-08T16:10:00Z")
            next_day = dashboard_view(kb, at="2026-11-09T16:10:00Z")
            self.assertEqual("settings.md", dashboard_inputs(kb)["source"])
            self.assertIn("2026-11-09T00:10:00+08:00", first["generated_at"])
            for fragment in (
                "current node state [S1]", "https://example.test/source#block", "版本 abc",
                "new progress", "keep this reminder",
            ):
                self.assertIn(fragment, first["markdown"])
            for fragment in ("陈旧", "节点提醒", "上次验证", "project-unwatched", "待办", "Overdue", "Due soon"):
                self.assertNotIn(fragment, first["markdown"])
            self.assertNotIn("new progress", next_day["markdown"])
            self.assertEqual(before, {path: path.read_bytes() for path in kb.rglob("*.md")})

    def test_view_rejects_private_inputs_aliased_by_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            kb = Path(directory).resolve()
            (kb / "settings.md").symlink_to(kb / "private")
            with self.assertRaises(DashboardError):
                dashboard_view(kb)
