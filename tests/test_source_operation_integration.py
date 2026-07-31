import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from source_capture import DEFAULT_MAX_ITEMS, SourceCaptureError  # noqa: E402
from source_chat_operations import (  # noqa: E402
    FeishuChatOperations,
    _auth_status as chat_auth_status,
    _summary as chat_summary,
)
from source_profiles import profile_relative_path, validate_profile  # noqa: E402
from source_operations import (  # noqa: E402
    AeolusOperations,
    BaseOperations,
    MeegoOperations,
    _where_filters,
    run_source_operation,
)


def operation_args(**overrides):
    values = {
        "operation": "capture",
        "source_type": "meego",
        "timeout": 30,
        "host": "",
        "url": "",
        "project_key": "",
        "base_token": "",
        "table_id": "",
        "view_id": "",
        "field": [],
        "source_uid": "",
        "kb": "",
        "max_items": DEFAULT_MAX_ITEMS,
        "report_id": [],
        "where": [],
        "filter_mode": "dashboard",
        "routine": "off",
        "out": "",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def meego_profile():
    return validate_profile(
        {
            "schema_version": "byteworker-source-profile/v2",
            "source_type": "meego",
            "source_uid": "meego:safety:view-42",
            "source_url": "https://project.feishu.cn/safety/view/view-42",
            "title": "安全需求视图",
            "selector": {"project_key": "safety", "view_id": "view-42"},
            "capture_policy": {
                "fields": ["updated_at", "name", "status"],
                "max_items": 500,
            },
            "routine": {"enabled": True, "cadence": "daily"},
        }
    )


def aeolus_profile():
    return validate_profile(
        {
            "schema_version": "byteworker-source-profile/v1",
            "source_type": "aeolus",
            "source_uid": "aeolus:cn:101:202:303",
            "source_url": (
                "https://data.bytedance.net/aeolus/pages/dashboard/202"
                "?appId=101&sheetId=303"
            ),
            "title": "风神交付看板",
            "coordinates": {
                "region": "cn",
                "app_id": 101,
                "dashboard_id": 202,
                "sheet_id": 303,
            },
            "capture": {
                "report_selector": {"mode": "include", "report_ids": [401]},
                "filters": {"mode": "dashboard", "where": []},
                "max_items_per_report": 500,
            },
            "routine": {"enabled": True, "cadence": "weekly"},
        }
    )


def chat_profile(*, since_last=False):
    capture_policy = {
        "since_last": since_last,
        "page_size": 20,
        "overlap_seconds": 30 if since_last else 0,
        "start": "" if since_last else "2026-07-29T09:00:00+08:00",
        "end": "2026-07-29T11:00:00+08:00",
    }
    return validate_profile(
        {
            "schema_version": "byteworker-source-profile/v2",
            "source_type": "feishu_chat",
            "source_uid": "oc_test",
            "source_url": "https://applink.feishu.cn/client/chat/oc_test",
            "title": "测试群",
            "selector": {"chat_id": "oc_test"},
            "capture_policy": capture_policy,
            "routine": {
                "enabled": since_last,
                "cadence": "daily" if since_last else None,
            },
        }
    )


class SourceOperationIntegrationTests(unittest.TestCase):
    def test_base_operation_replays_profile_page_and_record_limits(self):
        profile = validate_profile(
            {
                "schema_version": "byteworker-source-profile/v2",
                "source_type": "feishu_base",
                "source_uid": "feishu_base:bascn1:tbl1:vew1",
                "source_url": (
                    "https://example.test/base/bascn1?table=tbl1&view=vew1"
                ),
                "title": "需求视图",
                "selector": {
                    "app_token": "bascn1",
                    "table_id": "tbl1",
                    "view_id": "vew1",
                },
                "capture_policy": {
                    "fields": ["fld_title", "fld_status"],
                    "page_size": 100,
                    "max_records": 800,
                },
                "routine": {"enabled": True, "cadence": "weekly"},
            }
        )
        args = type(
            "Args",
            (),
            {
                "operation": "capture",
                "timeout": 30,
                "report_id": [],
                "where": [],
                "filter_mode": "dashboard",
                "url": "",
                "project_key": "",
                "base_token": "",
                "table_id": "",
                "view_id": "",
                "field": [],
                "source_uid": profile["source_uid"],
                "kb": "/tmp/test-kb",
                "max_items": 1000,
            },
        )()
        captured = {
            "source_uid": profile["source_uid"],
            "title": "provider title",
        }
        with (
            mock.patch("source_operations.load_profile", return_value=profile),
            mock.patch(
                "source_operations.capture_base",
                return_value=captured,
            ) as capture,
        ):
            result = BaseOperations().run(args, skill_root=ROOT)

        capture.assert_called_once()
        kwargs = capture.call_args.kwargs
        self.assertEqual("bascn1", kwargs["base_token"])
        self.assertEqual(["fld_status", "fld_title"], kwargs["fields"])
        self.assertEqual(100, kwargs["page_size"])
        self.assertEqual(800, kwargs["max_items"])
        self.assertEqual("需求视图", result["title"])
        self.assertIn("source_profile", result)

    def test_chat_profile_capture_wraps_pull_chat_as_bundle(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            kb = root / "kb"
            (kb / "sources").mkdir(parents=True)
            profile = validate_profile(
                {
                    "schema_version": "byteworker-source-profile/v2",
                    "source_type": "feishu_chat",
                    "source_uid": "oc_test",
                    "source_url": "https://bytedance.larkoffice.com/messenger/",
                    "title": "测试群",
                    "selector": {"chat_id": "oc_test"},
                    "capture_policy": {
                        "start": "2026-07-29T09:00:00+08:00",
                        "end": "2026-07-29T11:00:00+08:00",
                        "since_last": False,
                        "page_size": 20,
                        "overlap_seconds": 0,
                    },
                    "routine": {"enabled": False, "cadence": None},
                }
            )
            profile_path = kb / profile_relative_path(profile)
            profile_path.write_text(
                json.dumps(profile, ensure_ascii=False),
                encoding="utf-8",
            )
            fake_lark = root / "lark-cli"
            fake_lark.write_text(
                "#!/usr/bin/env python3\n"
                "import json\n"
                "print(json.dumps({'ok': True, 'data': {\n"
                "  'messages': [{\n"
                "    'message_id': 'om_test',\n"
                "    'chat_id': 'oc_test',\n"
                "    'create_time': '2026-07-29T10:00:00+08:00',\n"
                "    'sender': {'name': '甲', 'id': 'ou_test'},\n"
                "    'msg_type': 'text',\n"
                "    'content': '决定采用方案 A。'\n"
                "  }],\n"
                "  'has_more': False,\n"
                "  'page_token': ''\n"
                "}}))\n",
                encoding="utf-8",
            )
            fake_lark.chmod(0o755)
            output = root / "chat-bundle.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "bin" / "source.py"),
                    "capture",
                    "--source-type",
                    "feishu_chat",
                    "--kb",
                    str(kb),
                    "--source-uid",
                    "oc_test",
                    "--out",
                    str(output),
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                env={
                    **os.environ,
                    "BYTEWORKER_LARK_CLI_BIN": str(fake_lark),
                },
            )
            bundle = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(0, completed.returncode, completed.stdout)
        self.assertEqual("byteworker-source-bundle/v2", bundle["schema_version"])
        self.assertEqual("feishu_chat", bundle["identity"]["source_type"])
        self.assertEqual("chat:message:om_test", bundle["anchors"][0]["anchor_id"])
        self.assertEqual(1, bundle["provider_metadata"]["message_count"])
        self.assertEqual(20, bundle["provider_metadata"]["page_size"])


class SourceOperationMatrixTests(unittest.TestCase):
    def test_meego_profile_replay_owns_selector_fields_and_limits(self):
        profile = meego_profile()
        captured = {"source_uid": profile["source_uid"], "title": "provider title"}
        args = operation_args(
            source_uid=profile["source_uid"],
            kb="/tmp/test-kb",
        )
        with (
            mock.patch("source_operations._meego_runner", return_value=mock.Mock()),
            mock.patch("source_operations.load_profile", return_value=profile),
            mock.patch(
                "source_operations.capture_meego",
                return_value=captured,
            ) as capture,
        ):
            result = MeegoOperations().run(args, skill_root=ROOT)

        kwargs = capture.call_args.kwargs
        self.assertEqual("safety", kwargs["project_key"])
        self.assertEqual("view-42", kwargs["view_id"])
        self.assertEqual(["name", "status", "updated_at"], kwargs["fields"])
        self.assertEqual(500, kwargs["max_items"])
        self.assertEqual("安全需求视图", result["title"])
        self.assertEqual(
            str(profile_relative_path(profile)),
            result["source_profile"]["path"],
        )

    def test_profile_capture_rejects_cli_overrides_and_identity_drift(self):
        profile = meego_profile()
        override = operation_args(
            source_uid=profile["source_uid"],
            kb="/tmp/test-kb",
            url=profile["source_url"],
        )
        with (
            mock.patch("source_operations._meego_runner", return_value=mock.Mock()),
            self.assertRaises(SourceCaptureError) as caught,
        ):
            MeegoOperations().run(override, skill_root=ROOT)
        self.assertEqual("SOURCE_ARGUMENT_INVALID", caught.exception.code)

        drift = operation_args(source_uid=profile["source_uid"], kb="/tmp/test-kb")
        with (
            mock.patch("source_operations._meego_runner", return_value=mock.Mock()),
            mock.patch("source_operations.load_profile", return_value=profile),
            mock.patch(
                "source_operations.capture_meego",
                return_value={"source_uid": "meego:other:view"},
            ),
            self.assertRaises(SourceCaptureError) as caught,
        ):
            MeegoOperations().run(drift, skill_root=ROOT)
        self.assertEqual(
            "SOURCE_PROFILE_IDENTITY_MISMATCH",
            caught.exception.code,
        )

    def test_aeolus_register_parses_filters_and_saves_profile(self):
        profile = aeolus_profile()
        args = operation_args(
            operation="register",
            source_type="aeolus",
            kb="/tmp/test-kb",
            url=profile["source_url"],
            report_id=[401],
            where=['{"name":"部门","op":"in","val":["安全"]}'],
            filter_mode="explicit",
            routine="weekly",
            max_items=500,
        )
        client = object()
        with (
            mock.patch(
                "source_operations.aeolus_client_from_environment",
                return_value=client,
            ),
            mock.patch(
                "source_operations.build_aeolus_profile",
                return_value=profile,
            ) as build,
            mock.patch(
                "source_operations.save_profile",
                return_value={"status": "committed", "path": "sources/example.json"},
            ) as save,
        ):
            result = AeolusOperations().run(args, skill_root=ROOT)

        self.assertEqual("committed", result["status"])
        self.assertEqual(profile, result["profile"])
        self.assertEqual("安全", build.call_args.kwargs["where_filters"][0]["val"][0])
        self.assertEqual("weekly", build.call_args.kwargs["routine"])
        save.assert_called_once_with(Path("/tmp/test-kb"), profile, skill_root=ROOT)

    def test_aeolus_profile_capture_and_inspect_guards(self):
        profile = aeolus_profile()
        args = operation_args(
            source_type="aeolus",
            source_uid=profile["source_uid"],
            kb="/tmp/test-kb",
        )
        client = object()
        with (
            mock.patch(
                "source_operations.aeolus_client_from_environment",
                return_value=client,
            ),
            mock.patch("source_operations.load_profile", return_value=profile),
            mock.patch(
                "source_operations.capture_aeolus_from_profile",
                return_value={
                    "source_uid": profile["source_uid"],
                    "source_profile": {"revision": "abc"},
                },
            ) as capture,
        ):
            result = AeolusOperations().run(args, skill_root=ROOT)
        capture.assert_called_once_with(client=client, profile=profile)
        self.assertEqual(
            str(profile_relative_path(profile)),
            result["source_profile"]["path"],
        )

        invalid = operation_args(
            operation="inspect",
            source_type="aeolus",
            report_id=[401],
        )
        with (
            mock.patch(
                "source_operations.aeolus_client_from_environment",
                return_value=client,
            ),
            self.assertRaises(SourceCaptureError) as caught,
        ):
            AeolusOperations().run(invalid, skill_root=ROOT)
        self.assertEqual("SOURCE_ARGUMENT_INVALID", caught.exception.code)

    def test_filter_and_registry_errors_are_stable(self):
        self.assertEqual(
            [{"name": "部门", "value": "安全"}],
            _where_filters(['{"name":"部门","value":"安全"}']),
        )
        for value in ("not-json", "[]"):
            with self.subTest(value=value):
                with self.assertRaises(SourceCaptureError) as caught:
                    _where_filters([value])
                self.assertEqual("SOURCE_FILTER_INVALID", caught.exception.code)

        args = operation_args(source_type="unsupported")
        with self.assertRaises(SourceCaptureError) as caught:
            run_source_operation(args, skill_root=ROOT)
        self.assertEqual("SOURCE_TYPE_UNSUPPORTED", caught.exception.code)

    def test_chat_auth_summary_and_argument_guards(self):
        runner = mock.Mock()
        runner.run_status.return_value = SimpleNamespace(
            data={
                "identity": "user",
                "verified": True,
                "identities": {
                    "user": {
                        "available": True,
                        "status": "ready",
                        "tokenStatus": "valid",
                    }
                },
            }
        )
        status = chat_auth_status(runner)
        self.assertTrue(status["ready"])
        self.assertIsNone(status["action"])

        summary = chat_summary(
            "\n".join(
                (
                    "chat_id=oc_test",
                    "messages=2",
                    "pages=1",
                    "truncated=0",
                    "window=2026-07-29T09:00:00+08:00..2026-07-29T11:00:00+08:00",
                    "mode=explicit",
                    "transcript=/tmp/transcript.txt",
                    "locators=/tmp/locators.json",
                )
            )
        )
        self.assertEqual("2", summary["messages"])
        with self.assertRaises(SourceCaptureError) as caught:
            chat_summary("chat_id=oc_test\nmessages=2\n")
        self.assertEqual("SOURCE_INVALID_RESPONSE", caught.exception.code)

        for args in (
            operation_args(source_type="feishu_chat", kb="/tmp/kb"),
            operation_args(
                source_type="feishu_chat",
                kb="/tmp/kb",
                source_uid="oc_test",
            ),
            operation_args(
                source_type="feishu_chat",
                kb="/tmp/kb",
                source_uid="oc_test",
                out="/tmp/bundle.json",
                field=["name"],
            ),
        ):
            with self.subTest(args=args):
                with self.assertRaises(SourceCaptureError) as caught:
                    FeishuChatOperations._validate_capture_args(args)
                self.assertEqual("SOURCE_ARGUMENT_INVALID", caught.exception.code)

    def test_chat_command_replays_explicit_and_incremental_windows(self):
        explicit = chat_profile(since_last=False)
        args = operation_args(
            source_type="feishu_chat",
            kb="/tmp/kb",
            source_uid="oc_test",
            out="/tmp/bundle.json",
        )
        command = FeishuChatOperations._command(
            args,
            skill_root=ROOT,
            profile=explicit,
            transcript=Path("/tmp/transcript.txt"),
            locators=Path("/tmp/locators.json"),
        )
        self.assertIn("--start", command)
        self.assertNotIn("--since-last", command)
        self.assertEqual(
            explicit["capture_policy"]["start"],
            command[command.index("--start") + 1],
        )

        incremental = chat_profile(since_last=True)
        command = FeishuChatOperations._command(
            args,
            skill_root=ROOT,
            profile=incremental,
            transcript=Path("/tmp/transcript.txt"),
            locators=Path("/tmp/locators.json"),
        )
        self.assertIn("--since-last", command)
        self.assertNotIn("--start", command)
        self.assertEqual(
            incremental["capture_policy"]["end"],
            command[command.index("--end") + 1],
        )

    def test_pull_chat_since_last_parses_quoted_and_unquoted_windows(self):
        if shutil.which("jq") is None:
            self.fail("jq is required for pull-chat behavior tests")

        for quoted in (False, True):
            with self.subTest(quoted=quoted), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                kb = root / "kb"
                raw = kb / "raw_data"
                raw.mkdir(parents=True)
                window = (
                    "2026-07-29T09:00:00+08:00 .. "
                    "2026-07-29T11:00:00+08:00"
                )
                rendered_window = json.dumps(window) if quoted else window
                (raw / "chat.md").write_text(
                    textwrap.dedent(
                        f"""\
                        ---
                        source_chat_id: oc_test
                        source_window: {rendered_window}
                        ---
                        transcript
                        """
                    ),
                    encoding="utf-8",
                )
                call_log = root / "calls.jsonl"
                fake_lark = root / "lark-cli"
                fake_lark.write_text(
                    textwrap.dedent(
                        """\
                        #!/usr/bin/env python3
                        import json
                        import os
                        from pathlib import Path
                        import sys

                        args = sys.argv[1:]
                        with Path(os.environ["FAKE_LARK_LOG"]).open(
                            "a", encoding="utf-8"
                        ) as handle:
                            handle.write(json.dumps(args) + "\\n")
                        print(json.dumps({
                            "ok": True,
                            "data": {
                                "messages": [],
                                "has_more": False,
                                "page_token": "",
                            },
                        }))
                        """
                    ),
                    encoding="utf-8",
                )
                fake_lark.chmod(0o755)
                transcript = root / "transcript.txt"
                locators = root / "locators.json"
                completed = subprocess.run(
                    [
                        str(ROOT / "bin" / "pull-chat.sh"),
                        "--chat-id",
                        "oc_test",
                        "--since-last",
                        "--kb",
                        str(kb),
                        "--end",
                        "2026-07-30T20:30:00+08:00",
                        "--out",
                        str(transcript),
                        "--locators-out",
                        str(locators),
                    ],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env={
                        **os.environ,
                        "BYTEWORKER_LARK_CLI_BIN": str(fake_lark),
                        "BYTEWORKER_PYTHON_BIN": sys.executable,
                        "FAKE_LARK_LOG": str(call_log),
                    },
                    check=False,
                )
                self.assertEqual(0, completed.returncode, completed.stderr)
                calls = [
                    json.loads(line)
                    for line in call_log.read_text(encoding="utf-8").splitlines()
                ]
                self.assertEqual(1, len(calls))
                command = calls[0]
                self.assertEqual(
                    "2026-07-29T11:00:00+08:00",
                    command[command.index("--start") + 1],
                )


if __name__ == "__main__":
    unittest.main()
