import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ImInboxSummaryTests(unittest.TestCase):
    def setUp(self):
        if shutil.which("jq") is None:
            self.fail("jq is required for IM Inbox behavior tests")
        self.tempdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tempdir.name)
        self.skill = self.root / "skill"
        (self.skill / "bin").mkdir(parents=True)
        self.script = self.skill / "bin" / "im-inbox-summary.sh"
        shutil.copy2(ROOT / "bin" / "im-inbox-summary.sh", self.script)
        self.script.chmod(0o755)
        self.kb = self.root / "kb"
        self.kb.mkdir()
        (self.kb / "context.md").write_text(
            "# Context\n\n- 当前重点：Alpha 项目\n",
            encoding="utf-8",
        )
        (self.kb / "INDEX.md").write_text(
            "| id | title |\n|---|---|\n| project-alpha | Alpha 项目 |\n",
            encoding="utf-8",
        )
        self.fake_bin = self.root / "fake-bin"
        self.fake_bin.mkdir()
        self.call_log = self.root / "lark-calls.jsonl"
        self.write_fake_lark()

    def tearDown(self):
        self.tempdir.cleanup()

    def write_fake_lark(self):
        fake = self.fake_bin / "lark-cli"
        fake.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import json
                import os
                from pathlib import Path
                import sys

                args = sys.argv[1:]
                log = os.environ.get("FAKE_LARK_LOG", "")
                if log:
                    with Path(log).open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps(args, ensure_ascii=False) + "\\n")
                if args == ["--version"]:
                    print("lark-cli test")
                    raise SystemExit(0)

                scenario = os.environ.get("FAKE_LARK_SCENARIO", "happy")
                if "+chat-list" in args:
                    if scenario == "chat-list-fail":
                        print(json.dumps({"ok": False, "error": "permission denied"}))
                    else:
                        print(json.dumps({"ok": True, "data": {"chats": [], "has_more": False}}))
                    raise SystemExit(0)

                if "+messages-search" in args:
                    print(json.dumps({"ok": True, "data": {"messages": []}}))
                    raise SystemExit(0)

                if "+chat-messages-list" not in args:
                    print(json.dumps({"ok": False, "error": "unexpected command"}))
                    raise SystemExit(0)

                def message(message_id, created, text, *, at_me=False):
                    return {
                        "message_id": message_id,
                        "chat_id": "oc_test",
                        "create_time": created,
                        "sender": {"name": "测试用户", "id": "ou_test"},
                        "msg_type": "text",
                        "content": json.dumps({"text": text}, ensure_ascii=False),
                        "is_at_me": at_me,
                    }

                if scenario == "pagination":
                    page_token = ""
                    if "--page-token" in args:
                        page_token = args[args.index("--page-token") + 1]
                    if page_token:
                        messages = [
                            message("om_page_2", "2026-07-29T10:12:00+08:00", "第二页风险")
                        ]
                        data = {"messages": messages, "has_more": False, "page_token": ""}
                    else:
                        messages = [
                            message("om_page_0", "2026-07-29T10:01:00+08:00", "Alpha 风险"),
                            message("om_page_1", "2026-07-29T10:02:00+08:00", "请推进处理"),
                        ]
                        data = {"messages": messages, "has_more": True, "page_token": "next"}
                else:
                    data = {
                        "messages": [
                            message(
                                "om_risk",
                                "2026-07-29T10:01:00+08:00",
                                "Alpha 指标存在风险，需要今天给出回滚方案",
                                at_me=True,
                            ),
                            message(
                                "om_action",
                                "2026-07-29T10:03:00+08:00",
                                "请推进上线，明天给出结论",
                            ),
                            message("om_noise", "2026-07-29T10:04:00+08:00", "收到"),
                        ],
                        "has_more": False,
                        "page_token": "",
                    }
                print(json.dumps({"ok": True, "data": data}, ensure_ascii=False))
                """
            ),
            encoding="utf-8",
        )
        fake.chmod(0o755)

    def run_script(self, *args, scenario="happy", out=True):
        output = self.root / f"result-{len(list(self.root.glob('result-*.json')))}.json"
        command = [
            str(self.script),
            "--start",
            "2026-07-29T09:00:00+08:00",
            "--end",
            "2026-07-29T11:00:00+08:00",
            "--kb",
            str(self.kb),
            *args,
        ]
        if out:
            command.extend(["--out", str(output)])
        completed = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            env={
                **os.environ,
                "PATH": f"{self.fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
                "FAKE_LARK_SCENARIO": scenario,
                "FAKE_LARK_LOG": str(self.call_log),
                "TZ": "Asia/Shanghai",
            },
        )
        payload = json.loads(output.read_text(encoding="utf-8")) if output.exists() else None
        return completed, payload

    def test_forced_chat_filters_noise_and_builds_high_signal_thread(self):
        completed, payload = self.run_script(
            "--chat-id",
            "oc_test",
            "--keyword",
            "Alpha",
            "--no-chat-list",
            "--no-search",
            "--no-first-run-notice",
            "--no-repeat-run-notice",
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual(f"output={self.root / 'result-0.json'}\n", completed.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(1, payload["stats"]["candidate_chats"])
        self.assertEqual(3, payload["stats"]["raw_messages"])
        self.assertEqual(2, payload["stats"]["candidate_messages"])
        self.assertEqual(1, payload["stats"]["candidate_threads"])
        thread = payload["threads"][0]
        self.assertEqual("oc_test|2026-07-29T10:00", thread["thread_id"])
        self.assertEqual({"om_risk", "om_action"}, set(thread["message_ids"]))
        self.assertNotIn("om_noise", thread["message_ids"])
        self.assertFalse(thread["thread_truncated"])
        self.assertIn("Alpha", payload["keywords"])

    def test_per_chat_budget_marks_truncation_without_fetching_next_page(self):
        completed, payload = self.run_script(
            "--chat-id",
            "oc_test",
            "--no-chat-list",
            "--no-search",
            "--no-first-run-notice",
            "--no-repeat-run-notice",
            "--per-chat-limit",
            "2",
            scenario="pagination",
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual(1, payload["stats"]["truncated_chats"])
        self.assertTrue(payload["threads"][0]["thread_truncated"])
        self.assertTrue(
            any("单会话消息上限" in warning for warning in payload["warnings"])
        )
        calls = [
            json.loads(line)
            for line in self.call_log.read_text(encoding="utf-8").splitlines()
        ]
        message_calls = [call for call in calls if "+chat-messages-list" in call]
        self.assertEqual(1, len(message_calls))
        self.assertNotIn("--page-token", message_calls[0])

    def test_chat_list_failure_returns_bounded_empty_result_with_warning(self):
        completed, payload = self.run_script(
            "--no-search",
            "--no-first-run-notice",
            "--no-repeat-run-notice",
            scenario="chat-list-fail",
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual([], payload["threads"])
        self.assertEqual(0, payload["stats"]["candidate_chats"])
        self.assertTrue(any("chat-list 失败" in warning for warning in payload["warnings"]))
        self.assertTrue(any("没有发现可扫描会话" in warning for warning in payload["warnings"]))

    def test_first_and_repeat_run_notices_are_persisted_in_result(self):
        first, first_payload = self.run_script(
            "--chat-id",
            "oc_test",
            "--no-chat-list",
            "--no-search",
        )
        second, second_payload = self.run_script(
            "--chat-id",
            "oc_test",
            "--no-chat-list",
            "--no-search",
        )

        self.assertEqual(0, first.returncode, first.stderr)
        self.assertTrue(first_payload["first_run_notice"]["shown"])
        self.assertFalse(first_payload["repeat_run_notice"]["shown"])
        self.assertIn("首次运行说明", first.stderr)
        self.assertTrue((self.skill / ".im-inbox-summary-first-run-shown").exists())
        self.assertEqual(0, second.returncode, second.stderr)
        self.assertFalse(second_payload["first_run_notice"]["shown"])
        self.assertTrue(second_payload["repeat_run_notice"]["shown"])
        self.assertIn("重复运行提醒", second.stderr)
        self.assertTrue((self.skill / ".im-inbox-summary-last-run.json").exists())

    def test_dry_run_needs_no_lark_and_invalid_budget_fails_fast(self):
        completed, _ = self.run_script(
            "--dry-run",
            "--keyword",
            "Alpha",
            out=False,
        )
        payload = json.loads(completed.stdout)
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertTrue(payload["dry_run"])
        self.assertIn("Alpha", payload["keywords"])
        self.assertFalse((self.skill / ".im-inbox-summary-last-run.json").exists())

        invalid, invalid_payload = self.run_script("--max-chats", "0")
        self.assertEqual(1, invalid.returncode)
        self.assertIsNone(invalid_payload)
        self.assertIn("MAX_CHATS 必须是正整数", invalid.stderr)


if __name__ == "__main__":
    unittest.main()
