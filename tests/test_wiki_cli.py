import importlib.util
import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))


def load_script(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


WIKI_CLI = load_script("byteworker_wiki_cli", "bin/wiki.py")
DIGEST_JOB_CLI = load_script("byteworker_digest_job_cli", "bin/digest-job.py")


class FakeClient:
    def __init__(self):
        self.nodes = {
            "home": {
                "space_id": "space-1",
                "node_token": "home",
                "obj_token": "doc-home",
                "obj_type": "docx",
                "title": "首页",
                "has_child": True,
                "parent_node_token": "",
                "updated_at": "2026-07-01T00:00:00Z",
            },
            "leaf": {
                "space_id": "space-1",
                "node_token": "leaf",
                "obj_token": "doc-leaf",
                "obj_type": "docx",
                "title": "页面",
                "has_child": False,
                "parent_node_token": "home",
                "updated_at": "2026-07-02T00:00:00Z",
            },
        }

    def auth_status(self):
        return {"ready": True, "identity": "user"}

    def node_get(self, value):
        token = "home" if value.startswith("https://") else value
        return dict(self.nodes[token])

    def node_list(self, space_id, parent_node_token=""):
        if not parent_node_token:
            return [dict(self.nodes["home"])]
        if parent_node_token == "home":
            return [dict(self.nodes["leaf"])]
        return []


def job_selection():
    return {
        "schema_version": "byteworker-wiki-candidate-selection/v1",
        "space_id": "space-1",
        "space_url": "https://tenant.larkoffice.com/wiki/home",
        "root_node_token": "home",
        "tree_hash": "sha256:tree",
        "pages": [
            {
                "document_id": "doc-1",
                "node_token": "node-1",
                "title": "页面",
                "url": "https://tenant.larkoffice.com/wiki/node-1",
                "updated_at": "2026-07-01T00:00:00Z",
                "path_titles": ["页面"],
            }
        ],
    }


class WikiCliTests(unittest.TestCase):
    def test_parser_and_validation_helpers(self):
        self.assertEqual("auth-status", WIKI_CLI.parser().parse_args(["auth-status"]).operation)
        self.assertEqual(2, WIKI_CLI._positive("2"))
        with self.assertRaises(Exception):
            WIKI_CLI._positive("0")
        self.assertIsNotNone(WIKI_CLI._updated_after("2026-01-01T00:00:00Z"))
        for value in ("bad", "2026-01-01T00:00:00"):
            with self.assertRaises(WIKI_CLI.WikiError):
                WIKI_CLI._updated_after(value)
        with self.assertRaises(WIKI_CLI.WikiError):
            WIKI_CLI._validate_kb(ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            kb = Path(temporary)
            self.assertEqual(kb.resolve(), WIKI_CLI._validate_kb(kb))
            with self.assertRaises(WIKI_CLI.WikiError):
                WIKI_CLI._validate_output(Path("/var/not-byteworker/output.json"), kb)

    def test_all_read_and_state_operations(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as temporary:
            kb = Path(temporary)
            output = kb / "selection.json"
            with patch.object(WIKI_CLI, "LarkWikiClient", return_value=client):
                auth = WIKI_CLI._run(SimpleNamespace(operation="auth-status"))
                self.assertTrue(auth["ready"])
                inspected = WIKI_CLI._run(
                    SimpleNamespace(
                        operation="inspect",
                        url="https://tenant.larkoffice.com/wiki/home",
                    )
                )
                self.assertEqual("space-1", inspected["space_id"])
                scan = WIKI_CLI._run(
                    SimpleNamespace(
                        operation="scan",
                        kb=kb,
                        url="https://tenant.larkoffice.com/wiki/home",
                        source_uid=None,
                        root_node_token="",
                        max_nodes=100,
                        max_depth=None,
                    )
                )
                self.assertEqual(2, scan["node_count"])
                topics = WIKI_CLI._run(
                    SimpleNamespace(
                        operation="topics",
                        kb=kb,
                        space_id="space-1",
                        root_node_token="",
                        limit=5,
                    )
                )
                self.assertEqual(1, topics["total_topics"])
                candidates = WIKI_CLI._run(
                    SimpleNamespace(
                        operation="candidates",
                        kb=kb,
                        space_id="space-1",
                        root_node_token="",
                        max_pages=10,
                        updated_after=None,
                        out=output,
                        preview_limit=1,
                    )
                )
                self.assertEqual(2, candidates["page_count"])
                self.assertTrue(output.is_file())

    def test_profile_refresh_and_profile_create(self):
        client = FakeClient()
        profile = {
            "source_type": "feishu_wiki",
            "source_url": "https://tenant.larkoffice.com/wiki/home",
            "selector": {"root_node_token": "home"},
            "capture_policy": {
                "max_nodes": 10,
                "max_depth": None,
                "change_detection": "new_and_updated",
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            kb = Path(temporary)
            with (
                patch.object(WIKI_CLI, "LarkWikiClient", return_value=client),
                patch.object(WIKI_CLI, "load_profile", return_value=profile),
            ):
                receipt = WIKI_CLI._run(
                    SimpleNamespace(
                        operation="scan",
                        kb=kb,
                        url=None,
                        source_uid="feishu_wiki:space-1:home",
                        root_node_token="",
                        max_nodes=None,
                        max_depth=None,
                    )
                )
                self.assertEqual("subtree", receipt["scope"])
                with self.assertRaises(WIKI_CLI.WikiError):
                    WIKI_CLI._run(
                        SimpleNamespace(
                            operation="scan",
                            kb=kb,
                            url=None,
                            source_uid="uid",
                            root_node_token="override",
                            max_nodes=None,
                            max_depth=None,
                        )
                    )
            with (
                patch.object(WIKI_CLI, "LarkWikiClient", return_value=client),
                patch.object(
                    WIKI_CLI,
                    "save_profile",
                    return_value={"status": "committed"},
                ),
            ):
                created = WIKI_CLI._run(
                    SimpleNamespace(
                        operation="profile-create",
                        kb=kb,
                        url="https://tenant.larkoffice.com/wiki/home",
                        root_node_token="home",
                        max_depth=None,
                        max_nodes=100,
                        change_detection="structure_only",
                        routine="weekly",
                    )
                )
                self.assertEqual("feishu_wiki:space-1:home", created["source_uid"])

    def test_main_formats_success_and_structured_errors(self):
        with patch.object(
            WIKI_CLI,
            "_run",
            side_effect=WIKI_CLI.WikiError("WIKI_TEST", "failure"),
        ):
            output = io.StringIO()
            with redirect_stdout(output):
                code = WIKI_CLI.main(["auth-status"])
            self.assertEqual(1, code)
            self.assertEqual("WIKI_TEST", json.loads(output.getvalue())["error"]["code"])
        with patch.object(WIKI_CLI, "_run", return_value={"ok": True}):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(0, WIKI_CLI.main(["auth-status"]))
            self.assertTrue(json.loads(output.getvalue())["ok"])


class DigestJobCliTests(unittest.TestCase):
    def test_end_to_end_cli_dispatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            kb = Path(temporary)
            selection_path = kb / "selection.json"
            selection_path.write_text(json.dumps(job_selection()), encoding="utf-8")
            created = DIGEST_JOB_CLI._run(
                SimpleNamespace(
                    operation="create",
                    kb=kb,
                    selection=selection_path,
                    title="任务",
                    organization_node_id="org-x",
                    batch_size=1,
                )
            )
            job_id = created["job_id"]
            self.assertEqual(
                1,
                len(
                    DIGEST_JOB_CLI._run(
                        SimpleNamespace(operation="list", kb=kb, active=True)
                    )["jobs"]
                ),
            )
            DIGEST_JOB_CLI._run(
                SimpleNamespace(
                    operation="next",
                    kb=kb,
                    job_id=job_id,
                    limit=1,
                    lease_owner="test",
                    lease_seconds=30,
                )
            )
            marked = DIGEST_JOB_CLI._run(
                SimpleNamespace(
                    operation="mark",
                    kb=kb,
                    job_id=job_id,
                    document_id="doc-1",
                    status="committed",
                    raw_id="raw-1",
                    commit="abc",
                    error="",
                )
            )
            self.assertEqual("completed", marked["job_status"])
            status = DIGEST_JOB_CLI._run(
                SimpleNamespace(
                    operation="status",
                    kb=kb,
                    job_id=job_id,
                    limit=5,
                )
            )
            self.assertEqual("completed", status["status"])
            reconcile = DIGEST_JOB_CLI._run(
                SimpleNamespace(operation="reconcile", kb=kb, job_id=job_id)
            )
            self.assertEqual(0, reconcile["reconciled_count"])

    def test_cli_validation_cancel_and_main_error(self):
        self.assertEqual("list", DIGEST_JOB_CLI.parser().parse_args(["list", "--kb", "/tmp"]).operation)
        with self.assertRaises(Exception):
            DIGEST_JOB_CLI._positive("0")
        with self.assertRaises(DIGEST_JOB_CLI.DigestJobError):
            DIGEST_JOB_CLI._validate_kb(ROOT)
        with self.assertRaises(DIGEST_JOB_CLI.DigestJobError):
            DIGEST_JOB_CLI._read_selection(ROOT / "selection.json")
        with tempfile.TemporaryDirectory() as temporary:
            kb = Path(temporary)
            selection_path = kb / "selection.json"
            selection_path.write_text(json.dumps(job_selection()), encoding="utf-8")
            job_id = DIGEST_JOB_CLI._run(
                SimpleNamespace(
                    operation="create",
                    kb=kb,
                    selection=selection_path,
                    title="",
                    organization_node_id="",
                    batch_size=1,
                )
            )["job_id"]
            cancelled = DIGEST_JOB_CLI._run(
                SimpleNamespace(operation="cancel", kb=kb, job_id=job_id)
            )
            self.assertEqual("cancelled", cancelled["status"])
        with patch.object(
            DIGEST_JOB_CLI,
            "_run",
            side_effect=DIGEST_JOB_CLI.DigestJobError("JOB_TEST", "failure"),
        ):
            output = io.StringIO()
            with redirect_stdout(output):
                code = DIGEST_JOB_CLI.main(["list", "--kb", "/tmp"])
            self.assertEqual(1, code)
            self.assertEqual("JOB_TEST", json.loads(output.getvalue())["error"]["code"])


if __name__ == "__main__":
    unittest.main()
