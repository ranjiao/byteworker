import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from source_profiles import profile_relative_path, validate_profile  # noqa: E402
from source_operations import BaseOperations  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
