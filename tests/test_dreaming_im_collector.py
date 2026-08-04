from pathlib import Path
import json
import subprocess
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from dreaming_collectors.feishu_im import (  # noqa: E402
    FeishuImCollector,
    _default_command,
)
from dreaming_state import DreamingError  # noqa: E402


def message(index: int, *, chat_id="oc_test", chat_type="group"):
    return {
        "message_id": f"om_{index}",
        "chat_id": chat_id,
        "chat_type": chat_type,
        "create_time": f"2026-08-04T10:{index:02d}:00+08:00",
        "sender": {"id": "ou_sender", "name": "Sender"},
        "msg_type": "text",
        "content": {"text": f"message {index}"},
    }


class FakeCommand:
    def __init__(self):
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        if args[:2] == ["auth", "status"]:
            return {
                "ok": True,
                "data": {"identities": {"user": {"openId": "ou_me"}}},
            }
        if "+messages-search" in args:
            return {
                "ok": True,
                "data": {
                    "messages": [message(1, chat_id="oc_p2p", chat_type="p2p")],
                    "has_more": False,
                    "page_token": "",
                },
            }
        token = args[args.index("--page-token") + 1] if "--page-token" in args else ""
        return {
            "ok": True,
            "data": {
                "messages": [message(2 if token else 1)],
                "has_more": not bool(token),
                "page_token": "" if token else "next",
            },
        }


class DreamingImCollectorTests(unittest.TestCase):
    def test_principal_and_monitored_pagination(self):
        command = FakeCommand()
        collector = FeishuImCollector(command=command, page_size=20)
        self.assertEqual("user:ou_me", collector.principal())
        result = collector.collect_monitored(
            chat_ids=["oc_test"],
            start="2026-08-04T00:00:00+08:00",
            end="2026-08-04T12:00:00+08:00",
        )
        self.assertEqual("complete", result["coverage"]["status"])
        self.assertEqual(["om_1", "om_2"], [item["message_id"] for item in result["messages"]])
        message_calls = [call for call in command.calls if "+chat-messages-list" in call]
        self.assertEqual(2, len(message_calls))
        self.assertIn("--page-token", message_calls[1])
        self.assertTrue(all("--as" in call and "user" in call for call in message_calls))

    def test_discovery_is_best_effort_and_keeps_p2p(self):
        command = FakeCommand()
        result = FeishuImCollector(command=command).collect_discovery(
            start="2026-08-04T00:00:00+08:00",
            end="2026-08-04T12:00:00+08:00",
        )
        self.assertEqual("best_effort", result["coverage"]["status"])
        self.assertEqual("p2p", result["messages"][0]["chat_type"])
        call = next(call for call in command.calls if "+messages-search" in call)
        self.assertIn("", call)
        self.assertNotIn("--exclude-muted", call)

    def test_budget_truncation_creates_gap_without_persisting_page_token(self):
        command = FakeCommand()
        result = FeishuImCollector(command=command, max_messages=1).collect_monitored(
            chat_ids=["oc_test"],
            start="2026-08-04T00:00:00+08:00",
            end="2026-08-04T12:00:00+08:00",
        )
        self.assertEqual("partial", result["coverage"]["status"])
        self.assertEqual("chat_truncated", result["coverage"]["gaps"][0]["kind"])
        self.assertNotIn("page_token", result)

    def test_missing_principal_fails_closed(self):
        def command(args):
            return {"ok": True, "data": {"identities": {"user": {}}}}

        with self.assertRaises(DreamingError) as caught:
            FeishuImCollector(command=command).principal()
        self.assertEqual("SOURCE_AUTH_REQUIRED", caught.exception.code)

    def test_real_lark_auth_status_shape_without_ok_is_accepted(self):
        payload = {
            "identities": {
                "user": {
                    "status": "ready",
                    "available": True,
                    "openId": "ou_me",
                }
            },
            "identity": "user",
            "verified": True,
        }
        completed = subprocess.CompletedProcess(
            args=["lark-cli"],
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        )
        with mock.patch(
            "dreaming_collectors.feishu_im.subprocess.run",
            return_value=completed,
        ):
            result = _default_command(60)(
                ["auth", "status", "--verify", "--json"]
            )
        self.assertEqual(payload, result)

    def test_failed_command_keeps_non_null_error_details(self):
        completed = subprocess.CompletedProcess(
            args=["lark-cli"],
            returncode=2,
            stdout=json.dumps({"ok": False, "error": None}),
            stderr="permission denied",
        )
        with mock.patch(
            "dreaming_collectors.feishu_im.subprocess.run",
            return_value=completed,
        ):
            with self.assertRaises(DreamingError) as caught:
                _default_command(60)(["im", "+chat-messages-list"])
        self.assertEqual(
            "permission denied",
            caught.exception.details["error"]["message"],
        )


if __name__ == "__main__":
    unittest.main()
