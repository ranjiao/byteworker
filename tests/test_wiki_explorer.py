import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from wiki_explorer import (  # noqa: E402
    WikiError,
    LarkWikiClient,
    _error_from_process,
    enrich_snapshot_metadata,
    load_snapshot,
    save_snapshot,
    scan_tree,
    select_candidates,
    topic_summary,
    wiki_state_path,
)


class FakeWikiClient:
    def __init__(self):
        self.list_calls = []
        self.nodes = {
            "home": {
                "space_id": "space-1",
                "node_token": "home",
                "obj_token": "doc-home",
                "obj_type": "docx",
                "title": "首页",
                "has_child": False,
                "parent_node_token": "",
            },
            "topic": {
                "space_id": "space-1",
                "node_token": "topic",
                "obj_token": "doc-topic",
                "obj_type": "docx",
                "title": "检索",
                "has_child": True,
                "parent_node_token": "",
            },
            "leaf": {
                "space_id": "space-1",
                "node_token": "leaf",
                "obj_token": "doc-leaf",
                "obj_type": "docx",
                "title": "召回优化",
                "has_child": False,
                "parent_node_token": "topic",
                "updated_at": "2026-07-29T00:00:00Z",
            },
            "empty": {
                "space_id": "space-1",
                "node_token": "empty",
                "obj_token": "doc-empty",
                "obj_type": "docx",
                "title": "",
                "has_child": False,
                "parent_node_token": "topic",
            },
        }

    def auth_status(self):
        return {"ready": True}

    def node_get(self, value):
        token = "home" if value.startswith("https://") else value
        return dict(self.nodes[token])

    def node_list(self, space_id, parent_node_token=""):
        self.list_calls.append((space_id, parent_node_token))
        if not parent_node_token:
            return [dict(self.nodes["home"]), dict(self.nodes["topic"])]
        if parent_node_token == "topic":
            return [dict(self.nodes["leaf"]), dict(self.nodes["empty"])]
        return []


class WikiExplorerTests(unittest.TestCase):
    def test_keychain_failure_has_terminal_recovery_commands(self):
        error = _error_from_process(
            returncode=1,
            stdout="",
            stderr="keychain access blocked",
        )
        self.assertEqual("WIKI_KEYCHAIN_ACCESS_BLOCKED", error.code)
        self.assertIn("security unlock-keychain", error.hint)
        self.assertIn("keychain-downgrade", error.hint)

    def test_error_classification_is_stable(self):
        cases = (
            ("not logged in", "WIKI_AUTH_REQUIRED"),
            ("permission denied", "WIKI_PERMISSION_DENIED"),
            ("429 too many requests", "WIKI_RATE_LIMIT"),
            ('{"error":{"message":"bad request"}}', "WIKI_CLI_ERROR"),
        )
        for stderr, code in cases:
            with self.subTest(stderr=stderr):
                error = _error_from_process(
                    returncode=1,
                    stdout="",
                    stderr=stderr,
                )
                self.assertEqual(code, error.code)
                self.assertIn("code", error.as_dict())

    def test_lark_adapter_success_and_protocol_failures(self):
        client = LarkWikiClient(binary="fake")
        ok_node = subprocess.CompletedProcess(
            ["fake"],
            0,
            stdout=json.dumps(
                {
                    "ok": True,
                    "data": {
                        "space_id": "space-1",
                        "node_token": "node-1",
                    },
                }
            ),
            stderr="",
        )
        with patch("wiki_explorer.subprocess.run", return_value=ok_node) as run:
            self.assertEqual("node-1", client.node_get("node-1")["node_token"])
            self.assertIn("--as", run.call_args.args[0])

        ok_list = subprocess.CompletedProcess(
            ["fake"],
            0,
            stdout=json.dumps({"ok": True, "data": {"nodes": [{"node_token": "n"}]}}),
            stderr="",
        )
        with patch("wiki_explorer.subprocess.run", return_value=ok_list):
            self.assertEqual(
                "n",
                client.node_list("space-1", "parent")[0]["node_token"],
            )

        bad_payloads = (
            subprocess.CompletedProcess(["fake"], 0, stdout="not-json", stderr=""),
            subprocess.CompletedProcess(
                ["fake"],
                0,
                stdout=json.dumps({"ok": True, "data": []}),
                stderr="",
            ),
            subprocess.CompletedProcess(
                ["fake"],
                0,
                stdout=json.dumps({"ok": False, "error": "denied"}),
                stderr="",
            ),
        )
        for result in bad_payloads:
            with self.subTest(stdout=result.stdout):
                with patch("wiki_explorer.subprocess.run", return_value=result):
                    with self.assertRaises(WikiError):
                        client.node_get("node-1")

        invalid_nodes = subprocess.CompletedProcess(
            ["fake"],
            0,
            stdout=json.dumps({"ok": True, "data": {"nodes": "invalid"}}),
            stderr="",
        )
        with patch("wiki_explorer.subprocess.run", return_value=invalid_nodes):
            with self.assertRaises(WikiError):
                client.node_list("space-1")

    def test_lark_adapter_transport_and_auth_status(self):
        client = LarkWikiClient(binary="fake", timeout_seconds=1)
        with patch("wiki_explorer.subprocess.run", side_effect=FileNotFoundError):
            with self.assertRaises(WikiError) as caught:
                client.node_get("node")
            self.assertEqual("WIKI_CLI_NOT_FOUND", caught.exception.code)
            with self.assertRaises(WikiError):
                client.auth_status()
        with patch(
            "wiki_explorer.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["fake"], 1),
        ):
            with self.assertRaises(WikiError) as caught:
                client.node_get("node")
            self.assertEqual("WIKI_CLI_TIMEOUT", caught.exception.code)
            with self.assertRaises(WikiError) as caught:
                client.auth_status()
            self.assertEqual("WIKI_CLI_TIMEOUT", caught.exception.code)

        auth = subprocess.CompletedProcess(
            ["fake"],
            0,
            stdout=json.dumps(
                {
                    "identities": {
                        "user": {
                            "status": "ready",
                            "available": True,
                            "verified": True,
                            "tokenStatus": "valid",
                            "scope": "wiki:node:read",
                            "userName": "User",
                            "message": "ready",
                        }
                    }
                }
            ),
            stderr="",
        )
        with patch("wiki_explorer.subprocess.run", return_value=auth):
            status = client.auth_status()
            self.assertTrue(status["ready"])
            self.assertEqual("User", status["user_name"])

        keychain = subprocess.CompletedProcess(
            ["fake"],
            0,
            stdout=json.dumps(
                {
                    "identities": {
                        "user": {
                            "message": "Keychain access blocked",
                        }
                    }
                }
            ),
            stderr="",
        )
        with patch("wiki_explorer.subprocess.run", return_value=keychain):
            with self.assertRaises(WikiError) as caught:
                client.auth_status()
            self.assertEqual("WIKI_KEYCHAIN_ACCESS_BLOCKED", caught.exception.code)

        for result in (
            subprocess.CompletedProcess(["fake"], 1, stdout="", stderr="denied"),
            subprocess.CompletedProcess(["fake"], 0, stdout="bad", stderr=""),
        ):
            with patch("wiki_explorer.subprocess.run", return_value=result):
                with self.assertRaises(WikiError):
                    client.auth_status()

    def test_space_scan_lists_roots_even_when_homepage_has_no_child(self):
        client = FakeWikiClient()
        snapshot = scan_tree(
            client,
            url_or_token="https://tenant.larkoffice.com/wiki/home",
        )
        self.assertEqual(4, snapshot["coverage"]["node_count"])
        self.assertIn(("space-1", ""), client.list_calls)
        self.assertIn(("space-1", "topic"), client.list_calls)
        self.assertTrue(snapshot["coverage"]["complete"])

    def test_subtree_root_is_expanded_once_even_when_has_child_is_false(self):
        client = FakeWikiClient()
        snapshot = scan_tree(
            client,
            url_or_token="https://tenant.larkoffice.com/wiki/home",
            root_node_token="home",
        )
        self.assertEqual(1, snapshot["coverage"]["node_count"])
        self.assertEqual([("space-1", "home")], client.list_calls)

    def test_incomplete_depth_limited_scan_does_not_replace_state(self):
        client = FakeWikiClient()
        complete = scan_tree(
            client,
            url_or_token="https://tenant.larkoffice.com/wiki/home",
        )
        with tempfile.TemporaryDirectory() as temporary:
            kb = Path(temporary)
            first = save_snapshot(kb, complete)
            path = Path(first["state_path"])
            before = path.read_bytes()
            limited = scan_tree(
                client,
                url_or_token="https://tenant.larkoffice.com/wiki/home",
                max_depth=0,
            )
            with self.assertRaises(WikiError) as caught:
                save_snapshot(kb, limited)
            self.assertEqual("WIKI_SCAN_INCOMPLETE", caught.exception.code)
            self.assertEqual(before, path.read_bytes())

    def test_topics_are_compact_and_candidates_filter_empty_and_old(self):
        client = FakeWikiClient()
        snapshot = scan_tree(
            client,
            url_or_token="https://tenant.larkoffice.com/wiki/home",
        )
        topics = topic_summary(snapshot, limit=1)
        self.assertEqual(1, len(topics["topics"]))
        self.assertTrue(topics["truncated"])
        selection = select_candidates(
            client,
            snapshot,
            updated_after=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(["doc-leaf"], [item["document_id"] for item in selection["pages"]])
        self.assertNotIn("nodes", topics)

    def test_state_is_lazy_and_stored_outside_knowledge_graph(self):
        with tempfile.TemporaryDirectory() as temporary:
            kb = Path(temporary)
            expected = wiki_state_path(kb, space_id="space-1")
            self.assertFalse((kb / "state").exists())
            self.assertFalse(expected.exists())
            snapshot = scan_tree(
                FakeWikiClient(),
                url_or_token="https://tenant.larkoffice.com/wiki/home",
            )
            save_snapshot(kb, snapshot)
            self.assertEqual(snapshot["tree_hash"], load_snapshot(kb, space_id="space-1")["tree_hash"])
            self.assertFalse((kb / "knowledge").exists())
            self.assertEqual(
                "byteworker-wiki-tree-state/v1",
                json.loads(expected.read_text(encoding="utf-8"))["schema_version"],
            )

    def test_metadata_policy_only_expands_the_explicit_scope(self):
        client = FakeWikiClient()
        snapshot = scan_tree(
            client,
            url_or_token="https://tenant.larkoffice.com/wiki/home",
        )
        enrich_snapshot_metadata(
            client,
            snapshot,
            mode="new_pages",
            previous=None,
        )
        self.assertEqual(0, snapshot["metadata_policy"]["requested_node_count"])
        previous = json.loads(json.dumps(snapshot))
        previous["nodes"] = [
            item for item in previous["nodes"] if item["node_token"] != "leaf"
        ]
        enrich_snapshot_metadata(
            client,
            snapshot,
            mode="new_pages",
            previous=previous,
        )
        self.assertEqual(1, snapshot["metadata_policy"]["requested_node_count"])
        self.assertEqual(
            "2026-07-29T00:00:00Z",
            next(item for item in snapshot["nodes"] if item["node_token"] == "leaf")[
                "updated_at"
            ],
        )

    def test_scan_limits_invalid_anchor_and_deduplication(self):
        client = FakeWikiClient()
        for kwargs in ({"max_nodes": 0}, {"max_depth": -1}):
            with self.assertRaises(WikiError):
                scan_tree(
                    client,
                    url_or_token="https://tenant.larkoffice.com/wiki/home",
                    **kwargs,
                )
        broken = FakeWikiClient()
        broken.nodes["home"].pop("space_id")
        with self.assertRaises(WikiError):
            scan_tree(
                broken,
                url_or_token="https://tenant.larkoffice.com/wiki/home",
            )
        with self.assertRaises(WikiError) as caught:
            scan_tree(
                client,
                url_or_token="https://tenant.larkoffice.com/wiki/home",
                max_nodes=1,
            )
        self.assertEqual("WIKI_SCAN_LIMIT_EXCEEDED", caught.exception.code)

    def test_invalid_state_topics_candidates_and_output_fail_closed(self):
        client = FakeWikiClient()
        snapshot = scan_tree(
            client,
            url_or_token="https://tenant.larkoffice.com/wiki/home",
        )
        with self.assertRaises(WikiError):
            topic_summary(snapshot, limit=0)
        with self.assertRaises(WikiError):
            select_candidates(client, snapshot, max_pages=1)
        invalid_time = json.loads(json.dumps(snapshot))
        for item in invalid_time["nodes"]:
            item["updated_at"] = "not-a-time"
        for item in client.nodes.values():
            item["updated_at"] = "not-a-time"
        self.assertEqual(
            0,
            select_candidates(
                client,
                invalid_time,
                updated_after=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )["page_count"],
        )
        with tempfile.TemporaryDirectory() as temporary:
            kb = Path(temporary)
            path = wiki_state_path(kb, space_id="space-1")
            path.parent.mkdir(parents=True)
            for payload in ("not-json", "{}"):
                path.write_text(payload, encoding="utf-8")
                with self.assertRaises(WikiError):
                    load_snapshot(kb, space_id="space-1")
            path.write_text("not-json", encoding="utf-8")
            with self.assertRaises(WikiError):
                save_snapshot(kb, snapshot)
            with self.assertRaises(WikiError):
                from wiki_explorer import write_selection

                write_selection(kb, {"pages": []})

    def test_subtree_topic_fallback_and_unknown_metadata_mode(self):
        client = FakeWikiClient()
        snapshot = scan_tree(
            client,
            url_or_token="https://tenant.larkoffice.com/wiki/home",
            root_node_token="leaf",
        )
        topics = topic_summary(snapshot)
        self.assertEqual("leaf", topics["topics"][0]["node_token"])
        with self.assertRaises(WikiError):
            enrich_snapshot_metadata(
                client,
                snapshot,
                mode="unknown",
            )


if __name__ == "__main__":
    unittest.main()
