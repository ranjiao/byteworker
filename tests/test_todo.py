import importlib.util
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


MODULE_PATH = Path(__file__).parents[1] / "bin" / "todo.py"
SPEC = importlib.util.spec_from_file_location("byteworker_todo", MODULE_PATH)
todo = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = todo
SPEC.loader.exec_module(todo)


class TimeParsingTest(unittest.TestCase):
    def setUp(self):
        self.prefs = todo.Preferences()
        self.now = datetime(2026, 7, 23, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    def resolved(self, expression, kind="remind"):
        return todo.resolve_time(expression, self.now, self.prefs, kind).isoformat(timespec="minutes")

    def test_relative_days_and_chinese_clock(self):
        self.assertEqual(self.resolved("明天下午三点"), "2026-07-24T15:00+08:00")
        self.assertEqual(self.resolved("后天"), "2026-07-25T09:00+08:00")
        self.assertEqual(self.resolved("大后天"), "2026-07-26T09:00+08:00")
        self.assertEqual(self.resolved("三天后下午"), "2026-07-26T15:00+08:00")
        self.assertEqual(self.resolved("明早"), "2026-07-24T09:00+08:00")
        self.assertEqual(self.resolved("明晚八点"), "2026-07-24T20:00+08:00")

    def test_weekday_rules(self):
        self.assertEqual(self.resolved("周六"), "2026-07-25T09:00+08:00")
        self.assertEqual(self.resolved("本周六下午"), "2026-07-25T15:00+08:00")
        self.assertEqual(self.resolved("下周六"), "2026-08-01T09:00+08:00")

    def test_due_default_and_month_end(self):
        self.assertEqual(self.resolved("月底前", "due"), "2026-07-31T18:00+08:00")


class TodoStorageTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.kb = Path(self.tempdir.name)
        (self.kb / "context.md").write_text(
            "| 时区 | Asia/Shanghai |\n- 未指定具体时间的提醒：09:00\n"
            "- 只说截止日期、未指定具体时间：18:00\n- 临近到期窗口：24 小时\n",
            encoding="utf-8",
        )
        self.now = todo.local_now("2026-07-23 10:00", todo.load_preferences(self.kb))
        self.path = todo.ensure_initialized(self.kb, None)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_round_trip_and_check(self):
        preamble, todos = todo.load_todos(self.path)
        item = todo.Todo(
            "T-20260723-001",
            "提交周报",
            {
                "kind": "task",
                "status": "open",
                "created_at": todo.iso(self.now),
                "updated_at": todo.iso(self.now),
                "due_at": todo.iso(todo.resolve_time("明天", self.now, todo.load_preferences(self.kb), "due")),
                "remind_at": "",
                "time_expression": "明天",
                "snoozed_until": "",
                "source": "direct:user",
                "links": "",
                "reason": "",
                "last_reminded_at": "",
                "note": "",
            },
        )
        todos.append(item)
        todo.save(self.path, preamble, todos)
        _, loaded = todo.load_todos(self.path)
        self.assertEqual(loaded[0].title, "提交周报")
        alerts = todo.command_check(loaded, todo.local_now("2026-07-24 10:00", todo.load_preferences(self.kb)), 24)
        self.assertEqual(alerts[0]["category"], "due_soon")

    def test_commented_template_example_is_not_a_todo(self):
        template = Path(__file__).parents[1] / "templates" / "todo.md"
        self.path.unlink()
        todo.ensure_initialized(self.kb, template)
        _, loaded = todo.load_todos(self.path)
        self.assertEqual(loaded, [])

    def test_cli_add_and_complete_lifecycle(self):
        with redirect_stdout(io.StringIO()):
            todo.main([
                str(self.kb),
                "add",
                "--title",
                "提交周报",
                "--due",
                "明天",
                "--now",
                "2026-07-23 10:00",
            ])
        _, loaded = todo.load_todos(self.path)
        self.assertEqual(loaded[0].fields["due_at"], "2026-07-24T18:00:00+08:00")
        with redirect_stdout(io.StringIO()):
            todo.main([str(self.kb), "status", loaded[0].todo_id, "done", "--now", "2026-07-23 11:00"])
        _, completed = todo.load_todos(self.path)
        self.assertEqual(completed[0].status, "done")
        self.assertIn("## Completed\n\n### [x]", self.path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
