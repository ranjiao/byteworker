from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from dreaming_collectors.feishu_im import FeishuImCollector  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
