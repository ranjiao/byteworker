import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "bin" / "byteworker-cli.py"
LAUNCHER = ROOT / "bin" / "byteworker"


class MachineProtocolTests(unittest.TestCase):
    def run_cli(self, *args, env=None):
        return subprocess.run(
            [sys.executable, str(CLI), *map(str, args)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            env=env,
        )

    def test_todo_success_uses_stable_single_line_envelope(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = Path(temporary)
            subprocess.run(["git", "init", "-q"], cwd=kb, check=True)
            subprocess.run(
                ["git", "config", "user.email", "todo@example.test"],
                cwd=kb,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Todo Test"],
                cwd=kb,
                check=True,
            )
            subprocess.run(
                ["git", "commit", "--allow-empty", "-qm", "init"],
                cwd=kb,
                check=True,
            )
            result = self.run_cli(
                "todo",
                kb,
                "init",
                "--template",
                ROOT / "templates" / "todo.md",
            )
            self.assertEqual(0, result.returncode)
            self.assertEqual("", result.stderr)
            self.assertEqual(1, len(result.stdout.splitlines()))
            payload = json.loads(result.stdout)
            self.assertEqual("success", payload["status"])
            self.assertIsNone(payload["error"])
            self.assertEqual("byteworker-cli/v1", payload["context"]["protocol"])
            self.assertEqual("todo", payload["context"]["tool"])
            self.assertEqual("init", payload["context"]["operation"])
            self.assertTrue(payload["data"]["created"])
            self.assertEqual(
                "committed",
                payload["data"]["transaction"]["status"],
            )

    def test_argument_error_has_stable_code_and_exit_status(self):
        result = self.run_cli("kb-query", "search", "--kb", "/tmp/missing-query")
        self.assertEqual(2, result.returncode)
        payload = json.loads(result.stdout)
        self.assertEqual("error", payload["status"])
        self.assertIsNone(payload["data"])
        self.assertEqual("KB_QUERY_INPUT_ERROR", payload["error"]["code"])
        self.assertEqual(2, payload["error"]["details"]["exit_code"])

    def test_source_record_lookup_uses_machine_envelope(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = Path(temporary)
            (kb / "raw_data").mkdir()
            snapshot = {
                "schema_version": "byteworker-source-snapshot/v1",
                "source_type": "feishu_base",
                "source_uid": "feishu_base:base:table:view",
                "records": [
                    {
                        "record_id": "rec1",
                        "fields": {"标题": "风险治理需求"},
                    }
                ],
            }
            (kb / "raw_data/base.md").write_text(
                "---\n"
                "raw_id: raw-base\n"
                "ingested: 2026-07-29T14:00:00+08:00\n"
                "source_type: feishu_base\n"
                "source_uid: feishu_base:base:table:view\n"
                "digest_status: digested\n"
                "---\n\n"
                "```json\n"
                f"{json.dumps(snapshot, ensure_ascii=False)}\n"
                "```\n",
                encoding="utf-8",
            )
            result = self.run_cli(
                "kb-query",
                "source-record",
                "--kb",
                kb,
                "--title",
                "风险治理",
            )
        self.assertEqual(0, result.returncode)
        payload = json.loads(result.stdout)
        self.assertEqual("success", payload["status"])
        self.assertEqual("kb-query", payload["context"]["tool"])
        self.assertEqual("source-record", payload["context"]["operation"])
        self.assertEqual("rec1", payload["data"]["matches"][0]["record_id"])

    def test_facade_usage_error_is_also_an_envelope(self):
        result = self.run_cli("unknown-tool")
        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual("error", payload["status"])
        self.assertEqual("CLI_USAGE_ERROR", payload["error"]["code"])
        self.assertEqual("cli", payload["context"]["tool"])

    def test_registered_tool_help_passes_through_launcher(self):
        tools = [
            "todo",
            "source",
            "digest-txn",
            "kb-mutate",
            "kb-query",
            "context",
            "doctor",
            "wiki",
            "digest-job",
            "report-automation",
            "dreaming",
            "provenance-backfill",
            "index",
        ]
        for tool in tools:
            with self.subTest(tool=tool):
                result = subprocess.run(
                    [str(LAUNCHER), tool, "--help"],
                    cwd=ROOT,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                self.assertEqual(0, result.returncode, msg=result.stderr)
                self.assertTrue(result.stdout.startswith("usage:"), msg=result.stdout)
                self.assertNotIn("CLI_USAGE_ERROR", result.stdout)
                self.assertEqual("", result.stderr)

    def test_update_status_uses_same_envelope(self):
        result = self.run_cli("update-status")
        self.assertEqual(0, result.returncode)
        payload = json.loads(result.stdout)
        self.assertEqual("success", payload["status"])
        self.assertEqual(1, payload["data"]["version"])
        self.assertEqual("update-status", payload["context"]["tool"])

    def test_doctor_findings_are_attention_not_transport_error(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            result = self.run_cli("doctor", "scan", "--kb", temporary)
        self.assertEqual(2, result.returncode)
        payload = json.loads(result.stdout)
        self.assertEqual("attention", payload["status"])
        self.assertIsNone(payload["error"])
        self.assertGreater(payload["data"]["summary"]["error"], 0)
        self.assertEqual("scan", payload["context"]["operation"])

    def test_source_structured_error_code_survives_facade(self):
        result = self.run_cli(
            "source",
            "capture",
            "--source-type",
            "meego",
            "--project-key",
            "demo",
            "--view-id",
            "view-1",
        )
        self.assertEqual(1, result.returncode)
        payload = json.loads(result.stdout)
        self.assertEqual("error", payload["status"])
        self.assertEqual("SOURCE_FIELDS_REQUIRED", payload["error"]["code"])
        self.assertEqual("source", payload["context"]["tool"])
        self.assertEqual("capture", payload["context"]["operation"])

    def test_source_auth_status_treats_logged_out_as_actionable_state(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            fake = Path(temporary) / "meegle"
            fake.write_text(
                "#!/usr/bin/env python3\n"
                "import json, sys\n"
                "print(json.dumps({'authenticated': False, "
                "'host': None, 'reason': 'no local token'}))\n"
                "sys.exit(1)\n",
                encoding="utf-8",
            )
            fake.chmod(0o755)
            env = {
                **os.environ,
                "BYTEWORKER_MEEGLE_BIN": str(fake),
            }
            result = self.run_cli(
                "source",
                "auth-status",
                "--source-type",
                "meego",
                "--host",
                "project.feishu.cn",
                env=env,
            )
        self.assertEqual(0, result.returncode)
        payload = json.loads(result.stdout)
        self.assertEqual("success", payload["status"])
        self.assertFalse(payload["data"]["ready"])
        self.assertEqual("login", payload["data"]["action"]["kind"])
        self.assertEqual("auth-status", payload["context"]["operation"])

    def test_source_capabilities_exposes_three_independent_contract_sets(self):
        result = self.run_cli("source", "capabilities")
        self.assertEqual(0, result.returncode)
        payload = json.loads(result.stdout)
        data = payload["data"]
        self.assertIn("feishu_chat", data["operation_source_types"])
        self.assertIn("feishu_doc", data["profile_source_types"])
        self.assertIn("local_md", data["bundle_source_types"])
        self.assertEqual(
            "byteworker-source-bundle/v2",
            data["contract"],
        )

    def test_source_bundle_spec_exposes_adapter_contract(self):
        result = self.run_cli(
            "source",
            "bundle-spec",
            "--source-type",
            "feishu_minutes",
        )
        self.assertEqual(0, result.returncode)
        payload = json.loads(result.stdout)
        data = payload["data"]
        self.assertEqual("feishu_minutes", data["source_type"])
        self.assertEqual(
            ["source_uid", "source_url", "title", "transcript"],
            data["required_fields"],
        )
        self.assertFalse(data["transport"]["inline_json_supported"])
        self.assertEqual(["transcript"], data["artifact_fields"])
        self.assertIn("minute token", data["source_uid_rule"])

    def test_source_bundle_help_says_request_is_a_file_path(self):
        result = self.run_cli("source", "bundle", "--help")
        self.assertEqual(0, result.returncode)
        payload = json.loads(result.stdout)
        self.assertIn("request JSON 文件路径", payload["data"])
        self.assertIn("不接受内联", payload["data"])

    def test_source_bundle_materializes_local_file_through_registry(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            body = root / "source.md"
            request = root / "request.json"
            output = root / "bundle.json"
            body.write_text("# 原文\n", encoding="utf-8")
            request.write_text(
                json.dumps(
                    {
                        "source_uid": "direct-user:test",
                        "title": "用户确认原文",
                        "local_file": {"path": str(body)},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = self.run_cli(
                "source",
                "bundle",
                "--source-type",
                "local_md",
                "--request",
                request,
                "--out",
                output,
            )
            persisted = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(0, result.returncode)
        payload = json.loads(result.stdout)
        self.assertEqual("success", payload["status"])
        self.assertEqual(
            "byteworker-source-bundle/v2",
            persisted["schema_version"],
        )
        self.assertEqual("local_md", persisted["identity"]["source_type"])
        self.assertEqual(str(output.resolve()), payload["data"]["output"])

    def test_source_bundle_rejects_provider_request_shape_with_stable_error(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            request = root / "request.json"
            request.write_text('{"unexpected":"value"}', encoding="utf-8")
            result = self.run_cli(
                "source",
                "bundle",
                "--source-type",
                "local_md",
                "--request",
                request,
                "--out",
                root / "bundle.json",
            )
        self.assertEqual(1, result.returncode)
        payload = json.loads(result.stdout)
        self.assertEqual(
            "SOURCE_BUNDLE_REQUEST_INVALID",
            payload["error"]["code"],
        )

    def test_source_bundle_rejects_inline_json_with_actionable_error(self):
        result = self.run_cli(
            "source",
            "bundle",
            "--source-type",
            "feishu_minutes",
            "--request",
            '{"source_uid":"obcn_test"}',
            "--out",
            "/tmp/byteworker-inline-bundle.json",
        )
        self.assertEqual(1, result.returncode)
        payload = json.loads(result.stdout)
        self.assertEqual(
            "SOURCE_BUNDLE_REQUEST_INLINE_UNSUPPORTED",
            payload["error"]["code"],
        )
        self.assertIn("临时目录", payload["error"]["hint"])

    def test_source_bundle_missing_request_has_specific_error(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            missing = Path(temporary) / "missing-request.json"
            result = self.run_cli(
                "source",
                "bundle",
                "--source-type",
                "feishu_doc",
                "--request",
                missing,
                "--out",
                Path(temporary) / "bundle.json",
            )
        self.assertEqual(1, result.returncode)
        payload = json.loads(result.stdout)
        self.assertEqual(
            "SOURCE_BUNDLE_REQUEST_NOT_FOUND",
            payload["error"]["code"],
        )
        self.assertIn("bundle-spec", payload["error"]["hint"])

    def test_source_bundle_rejects_inline_capture_to_avoid_dual_truth(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            request = root / "request.json"
            request.write_text(
                json.dumps(
                    {
                        "capture": {"source_uid": "inline"},
                        "capture_path": str(root / "capture.json"),
                    }
                ),
                encoding="utf-8",
            )
            result = self.run_cli(
                "source",
                "bundle",
                "--source-type",
                "meego",
                "--request",
                request,
                "--out",
                root / "bundle.json",
            )
        self.assertEqual(1, result.returncode)
        payload = json.loads(result.stdout)
        self.assertEqual(
            "SOURCE_BUNDLE_REQUEST_INVALID",
            payload["error"]["code"],
        )
        self.assertIn("内联 capture", payload["error"]["message"])

    def test_source_diff_uses_same_envelope_and_writes_summary(self):
        def capture(records):
            return {
                "source_type": "meego",
                "source_uid": "meego:project:view",
                "content_hash": "sha256:test",
                "snapshot": {
                    "schema_version": "byteworker-source-snapshot/v1",
                    "source_type": "meego",
                    "source_uid": "meego:project:view",
                    "coordinates": {
                        "project_key": "project",
                        "view_id": "view",
                    },
                    "fields": ["name", "status"],
                    "records": records,
                },
            }

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            previous = root / "previous.json"
            current = root / "current.json"
            output = root / "diff.json"
            previous.write_text(
                json.dumps(
                    capture(
                        [
                            {
                                "work_item_id": "1",
                                "name": "A",
                                "status": "doing",
                            }
                        ]
                    )
                ),
                encoding="utf-8",
            )
            current.write_text(
                json.dumps(
                    capture(
                        [
                            {
                                "work_item_id": "1",
                                "name": "A",
                                "status": "done",
                            }
                        ]
                    )
                ),
                encoding="utf-8",
            )
            result = self.run_cli(
                "source",
                "diff",
                "--previous",
                previous,
                "--current",
                current,
                "--out",
                output,
            )
            diff = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(0, result.returncode)
        payload = json.loads(result.stdout)
        self.assertEqual("success", payload["status"])
        self.assertEqual("source", payload["context"]["tool"])
        self.assertEqual("diff", payload["context"]["operation"])
        self.assertEqual(1, payload["data"]["summary"]["changed"])
        self.assertEqual(["status"], diff["changes"][0]["changed_paths"])

    def test_index_rebuild_has_dry_run_and_apply_machine_receipts(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = Path(temporary)
            for directory in (
                "people",
                "projects",
                "areas",
                "orgs",
                "events",
                "decisions",
                "readings",
            ):
                (kb / "knowledge" / directory).mkdir(parents=True)
            for directory in ("raw_data", "sources", "provenance", "journal"):
                (kb / directory).mkdir()
            for directory in ("daily", "weekly", "im"):
                (kb / "reports" / directory).mkdir(parents=True)
            for name in ("context.md", "todo.md", "dashboard.md"):
                (kb / name).write_text(f"# {name}\n", encoding="utf-8")
            (kb / "INDEX.md").write_text("# stale\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q"], cwd=kb, check=True)
            subprocess.run(
                ["git", "config", "user.email", "index@example.test"],
                cwd=kb,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Index Test"],
                cwd=kb,
                check=True,
            )
            subprocess.run(["git", "add", "."], cwd=kb, check=True)
            subprocess.run(["git", "commit", "-qm", "init"], cwd=kb, check=True)

            preview = self.run_cli(
                "index",
                "rebuild",
                "--kb",
                kb,
                "--dry-run",
            )
            self.assertEqual("# stale\n", (kb / "INDEX.md").read_text(encoding="utf-8"))
            applied = self.run_cli("index", "rebuild", "--kb", kb)

            preview_payload = json.loads(preview.stdout)
            applied_payload = json.loads(applied.stdout)
            self.assertEqual(0, preview.returncode)
            self.assertEqual("would_change", preview_payload["data"]["status"])
            self.assertFalse(preview_payload["data"]["git_commit_created"])
            self.assertEqual(0, applied.returncode)
            self.assertEqual("rebuilt", applied_payload["data"]["status"])
            self.assertTrue(applied_payload["data"]["git_commit_created"])
            self.assertIn(
                "# 知识库索引",
                (kb / "INDEX.md").read_text(encoding="utf-8"),
            )

    def test_source_diff_can_load_previous_snapshot_from_kb(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            kb = root / "kb"
            raw = kb / "raw_data"
            raw.mkdir(parents=True)
            snapshot = {
                "schema_version": "byteworker-source-snapshot/v1",
                "source_type": "meego",
                "source_uid": "meego:project:view",
                "records": [{"work_item_id": "1", "status": "doing"}],
            }
            (raw / "previous.md").write_text(
                "---\n"
                "raw_id: raw-previous\n"
                "ingested: 2026-07-29T09:00:00+08:00\n"
                "source_type: meego\n"
                "source_uid: meego:project:view\n"
                "digest_status: digested\n"
                "---\n\n"
                "```json\n"
                f"{json.dumps(snapshot, ensure_ascii=False)}\n"
                "```\n",
                encoding="utf-8",
            )
            current = root / "current.json"
            current.write_text(
                json.dumps(
                    {
                        "source_type": "meego",
                        "source_uid": "meego:project:view",
                        "content_hash": "sha256:current",
                        "snapshot": {
                            **snapshot,
                            "records": [
                                {"work_item_id": "1", "status": "done"}
                            ],
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = self.run_cli(
                "source",
                "diff",
                "--kb",
                kb,
                "--source-uid",
                "meego:project:view",
                "--current",
                current,
            )

        self.assertEqual(0, result.returncode)
        payload = json.loads(result.stdout)
        self.assertEqual("success", payload["status"])
        self.assertEqual(1, payload["data"]["summary"]["changed"])
        self.assertEqual(
            "raw-previous",
            payload["data"]["previous_raw"]["raw_id"],
        )


if __name__ == "__main__":
    unittest.main()
