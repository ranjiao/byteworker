import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest import mock
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
        self.tempdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.kb = Path(self.tempdir.name)
        (self.kb / "context.md").write_text(
            "| 时区 | Asia/Shanghai |\n- 未指定具体时间的提醒：09:00\n"
            "- 只说截止日期、未指定具体时间：18:00\n- 临近到期窗口：24 小时\n",
            encoding="utf-8",
        )
        self.now = todo.local_now("2026-07-23 10:00", todo.load_preferences(self.kb))
        self.path = todo.ensure_initialized(self.kb, None)
        subprocess.run(["git", "init", "-q"], cwd=self.kb, check=True)
        subprocess.run(
            ["git", "config", "user.email", "todo@example.test"],
            cwd=self.kb,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Todo Test"],
            cwd=self.kb,
            check=True,
        )
        subprocess.run(["git", "add", "."], cwd=self.kb, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=self.kb, check=True)

    def tearDown(self):
        self.tempdir.cleanup()

    def run_cli(self, *args):
        output = io.StringIO()
        with redirect_stdout(output):
            return_code = todo.main([str(self.kb), *args])
        self.assertEqual(0, return_code)
        return json.loads(output.getvalue())

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
        created = self.run_cli(
            "add",
            "--title",
            "提交周报",
            "--due",
            "明天",
            "--now",
            "2026-07-23 10:00",
        )
        self.assertEqual("committed", created["transaction"]["status"])
        _, loaded = todo.load_todos(self.path)
        self.assertEqual(loaded[0].fields["due_at"], "2026-07-24T18:00:00+08:00")
        self.run_cli(
            "status",
            loaded[0].todo_id,
            "done",
            "--now",
            "2026-07-23 11:00",
        )
        _, completed = todo.load_todos(self.path)
        self.assertEqual(completed[0].status, "done")
        self.assertIn("## Completed\n\n### [x]", self.path.read_text(encoding="utf-8"))
        self.assertEqual(
            "",
            subprocess.run(
                ["git", "status", "--short"],
                cwd=self.kb,
                text=True,
                stdout=subprocess.PIPE,
                check=True,
            ).stdout,
        )

    def test_list_scopes_include_active_done_and_cancelled_items(self):
        first = self.run_cli(
            "add",
            "--title",
            "提交周报",
            "--now",
            "2026-07-23 10:00",
        )
        second = self.run_cli(
            "add",
            "--title",
            "确认排期",
            "--now",
            "2026-07-23 10:01",
        )
        active = self.run_cli("list", "--scope", "active")
        self.assertEqual({"提交周报", "确认排期"}, {item["title"] for item in active})

        self.run_cli(
            "status",
            first["id"],
            "done",
            "--now",
            "2026-07-23 11:00",
        )
        self.run_cli(
            "status",
            second["id"],
            "cancelled",
            "--now",
            "2026-07-23 11:01",
        )
        self.assertEqual([], self.run_cli("list", "--scope", "active"))
        completed = self.run_cli("list", "--scope", "completed")
        self.assertEqual({"done", "cancelled"}, {item["status"] for item in completed})
        self.assertEqual(2, len(self.run_cli("list", "--scope", "all")))

    def test_snooze_mark_reminded_and_edit_cover_reminder_lifecycle(self):
        item = self.run_cli(
            "add",
            "--title",
            "跟进发布",
            "--due",
            "明天",
            "--remind",
            "今天上午九点",
            "--note",
            "初始备注",
            "--now",
            "2026-07-23 08:00",
        )
        alerts = self.run_cli(
            "check",
            "--now",
            "2026-07-23 10:00",
        )
        self.assertEqual("reminder", alerts[0]["category"])

        snoozed = self.run_cli(
            "snooze",
            item["id"],
            "明天下午三点",
            "--now",
            "2026-07-23 10:00",
        )
        self.assertEqual(
            "2026-07-24T15:00:00+08:00",
            snoozed["snoozed_until"],
        )
        self.assertEqual(
            [],
            self.run_cli("check", "--now", "2026-07-24 14:00"),
        )
        alerts = self.run_cli("check", "--now", "2026-07-24 16:00")
        self.assertEqual("reminder", alerts[0]["category"])

        reminded = self.run_cli(
            "mark-reminded",
            item["id"],
            "--now",
            "2026-07-24 16:00",
        )
        self.assertEqual(
            "2026-07-24T16:00:00+08:00",
            reminded["last_reminded_at"],
        )
        self.assertEqual(
            [],
            self.run_cli("check", "--now", "2026-07-24 16:30"),
        )

        edited = self.run_cli(
            "edit",
            item["id"],
            "--title",
            "跟进正式发布",
            "--clear-due",
            "--clear-remind",
            "--note",
            "已调整",
            "--now",
            "2026-07-24 17:00",
        )
        self.assertEqual("跟进正式发布", edited["title"])
        self.assertEqual("", edited["due_at"])
        self.assertEqual("", edited["remind_at"])
        self.assertEqual("已调整", edited["note"])

    def test_invalid_id_time_and_corrupt_storage_fail_without_rewrite(self):
        item = self.run_cli(
            "add",
            "--title",
            "保护内容",
            "--now",
            "2026-07-23 10:00",
        )
        before = self.path.read_bytes()
        with self.assertRaisesRegex(SystemExit, "未找到 todo"):
            todo.main(
                [
                    str(self.kb),
                    "status",
                    "T-20260723-999",
                    "done",
                    "--now",
                    "2026-07-23 11:00",
                ]
            )
        self.assertEqual(before, self.path.read_bytes())

        with self.assertRaisesRegex(SystemExit, "无法解析时间表达"):
            todo.main(
                [
                    str(self.kb),
                    "edit",
                    item["id"],
                    "--due",
                    "某个不确定时间",
                    "--now",
                    "2026-07-23 11:00",
                ]
            )
        self.assertEqual(before, self.path.read_bytes())

        self.path.write_text("# TODO\n\n## Active\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Active / Completed"):
            todo.load_todos(self.path)

    def test_commit_failure_restores_todo_journal_and_git_index(self):
        before = self.path.read_bytes()
        real_git = todo._git

        def fail_commit(kb_dir, *args, check=True):
            if args[:1] == ("commit",):
                raise todo.TodoTransactionError("forced commit failure")
            return real_git(kb_dir, *args, check=check)

        with mock.patch.object(todo, "_git", side_effect=fail_commit):
            with self.assertRaisesRegex(SystemExit, "forced commit failure"):
                todo.main(
                    [
                        str(self.kb),
                        "add",
                        "--title",
                        "rollback me",
                        "--now",
                        "2026-07-23 10:00",
                    ]
                )

        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(
            "",
            subprocess.run(
                ["git", "status", "--short"],
                cwd=self.kb,
                text=True,
                stdout=subprocess.PIPE,
                check=True,
            ).stdout,
        )


if __name__ == "__main__":
    unittest.main()
