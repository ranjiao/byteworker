import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "pull_doc_comments", ROOT / "bin" / "pull_doc_comments.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class Result:
    def __init__(self, payload, returncode=0, stderr=""):
        self.returncode = returncode
        self.stdout = json.dumps(payload)
        self.stderr = stderr


class FakeRunner:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        return self.responses.pop(0)


class PullDocCommentsTests(unittest.TestCase):
    def test_fetches_all_comment_pages_and_complete_reply_chain(self):
        comment_a = {
            "comment_id": "c1",
            "create_time": 20,
            "has_more": True,
            "page_token": "reply-next",
            "is_solved": True,
            "reply_list": {"replies": [{"reply_id": "truncated"}]},
        }
        comment_b = {
            "comment_id": "c2",
            "create_time": 10,
            "is_solved": False,
            "reply_list": {
                "replies": [
                    {"reply_id": "r3", "create_time": 30, "content": {"text": "B"}}
                ]
            },
        }
        runner = FakeRunner(
            [
                Result(
                    {
                        "ok": True,
                        "data": {
                            "file_token": "doc-token",
                            "file_type": "docx",
                            "has_more": True,
                            "page_token": "comment-next",
                            "items": [comment_a],
                        },
                    }
                ),
                Result(
                    {
                        "ok": True,
                        "data": {
                            "has_more": True,
                            "page_token": "reply-page-2",
                            "items": [
                                {
                                    "reply_id": "r2",
                                    "create_time": 22,
                                    "content": {"text": "second"},
                                }
                            ],
                        },
                    }
                ),
                Result(
                    {
                        "ok": True,
                        "data": {
                            "has_more": False,
                            "items": [
                                {
                                    "reply_id": "r1",
                                    "create_time": 21,
                                    "content": {"text": "first"},
                                }
                            ],
                        },
                    }
                ),
                Result(
                    {
                        "ok": True,
                        "data": {
                            "file_token": "doc-token",
                            "file_type": "docx",
                            "has_more": False,
                            "items": [comment_b],
                        },
                    }
                ),
            ]
        )

        snapshot = MODULE.fetch_snapshot(
            "https://example.test/wiki/abc",
            runner=runner,
            fetched_at="2026-07-27T10:00:00+08:00",
        )

        self.assertEqual(["c2", "c1"], [x["comment_id"] for x in snapshot["comments"]])
        replies = snapshot["comments"][1]["reply_list"]["replies"]
        self.assertEqual(["r1", "r2"], [x["reply_id"] for x in replies])
        self.assertEqual(1, snapshot["coverage"]["expanded_reply_threads"])
        self.assertEqual(2, snapshot["comment_count"])
        self.assertTrue(snapshot["comment_hash"].startswith("sha256:"))
        commands = [" ".join(call) for call in runner.calls]
        self.assertIn("--solved-status all", commands[0])
        self.assertIn("--need-relation", commands[0])
        self.assertIn("--page-token comment-next", commands[-1])

    def test_hash_is_independent_of_fetch_time_and_api_order(self):
        first_runner = FakeRunner(
            [
                Result(
                    {
                        "data": {
                            "file_token": "t",
                            "file_type": "docx",
                            "items": [
                                {
                                    "comment_id": "later",
                                    "create_time": 2,
                                    "reply_list": {"replies": []},
                                },
                                {
                                    "comment_id": "earlier",
                                    "create_time": 1,
                                    "reply_list": {"replies": []},
                                },
                            ],
                        }
                    }
                )
            ]
        )
        second_runner = FakeRunner(
            [
                Result(
                    {
                        "data": {
                            "file_token": "t",
                            "file_type": "docx",
                            "items": [
                                {
                                    "comment_id": "earlier",
                                    "create_time": 1,
                                    "reply_list": {"replies": []},
                                },
                                {
                                    "comment_id": "later",
                                    "create_time": 2,
                                    "reply_list": {"replies": []},
                                },
                            ],
                        }
                    }
                )
            ]
        )
        first = MODULE.fetch_snapshot(
            "https://example.test/docx/a", runner=first_runner, fetched_at="one"
        )
        second = MODULE.fetch_snapshot(
            "https://example.test/docx/a", runner=second_runner, fetched_at="two"
        )
        self.assertEqual(first["comment_hash"], second["comment_hash"])
        self.assertNotEqual(first["fetched_at"], second["fetched_at"])

    def test_rejects_broken_pagination(self):
        runner = FakeRunner(
            [
                Result(
                    {
                        "data": {
                            "file_token": "t",
                            "file_type": "docx",
                            "has_more": True,
                            "items": [],
                        }
                    }
                )
            ]
        )
        with self.assertRaises(MODULE.CommentFetchError):
            MODULE.fetch_snapshot("https://example.test/docx/a", runner=runner)

    def test_rejects_reply_expansion_without_file_identity(self):
        runner = FakeRunner(
            [
                Result(
                    {
                        "data": {
                            "items": [
                                {
                                    "comment_id": "c1",
                                    "has_more": True,
                                    "reply_list": {"replies": []},
                                }
                            ]
                        }
                    }
                )
            ]
        )
        with self.assertRaises(MODULE.CommentFetchError):
            MODULE.fetch_snapshot("https://example.test/docx/a", runner=runner)


if __name__ == "__main__":
    unittest.main()
