import copy
import json
import sys
import tempfile
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from digest_txn import compute_payload  # noqa: E402
from sources.adapters.text import (  # noqa: E402
    build_feishu_chat_bundle,
    build_feishu_minutes_bundle,
    build_local_markdown_bundle,
    build_web_bundle,
    feishu_chat_bundle_to_transaction_source,
    feishu_minutes_bundle_to_transaction_source,
    local_markdown_bundle_to_transaction_source,
    web_bundle_to_transaction_source,
)
from sources.models import BUNDLE_SCHEMA, SourceBundleError  # noqa: E402


class TextSourceAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, name: str, content: str) -> Path:
        path = self.root / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_chat_normalizes_pull_chat_locator_artifact(self):
        transcript = self.write(
            "chat.txt",
            "=== [2026-07-29T10:00:00+08:00] 甲\n决定采用方案 A。\n",
        )
        locator_artifact = {
            "schema_version": "byteworker-source-locators/v1",
            "source_type": "feishu_chat",
            "source_chat_id": "oc_test",
            "source_window": (
                "2026-07-29T09:00:00+08:00 .. "
                "2026-07-29T11:00:00+08:00"
            ),
            "anchors": [
                {
                    "anchor_id": "chat:message:om_1",
                    "kind": "chat_message",
                    "precision": "exact",
                    "open_url": "https://example.test/message/om_1",
                    "source_time": "2026-07-29T10:00:00+08:00",
                    "quote": "决定采用方案 A。",
                    "locator": {
                        "chat_id": "oc_test",
                        "message_id": "om_1",
                        "thread_id": "",
                    },
                }
            ],
        }
        locator_path = self.write(
            "locators.json",
            json.dumps(locator_artifact, ensure_ascii=False),
        )
        before = copy.deepcopy(locator_artifact)

        bundle = build_feishu_chat_bundle(
            source_uid="oc_test",
            title="测试群",
            source_window=locator_artifact["source_window"],
            transcript={"path": str(transcript)},
            locator_artifact=locator_path,
            provider_metadata={"message_count": 1},
        )
        value = bundle.to_dict()
        source = feishu_chat_bundle_to_transaction_source(bundle)

        self.assertEqual(before, locator_artifact)
        self.assertEqual(BUNDLE_SCHEMA, value["schema_version"])
        self.assertEqual("body", value["components"][0]["kind"])
        self.assertEqual(
            "chat:message:om_1",
            value["anchors"][0]["anchor_id"],
        )
        self.assertEqual("body", value["anchors"][0]["component"])
        self.assertEqual(1, value["provider_metadata"]["anchor_count"])
        self.assertEqual("feishu_chat", source["type"])
        self.assertEqual("oc_test", source["uid"])
        self.assertEqual(
            locator_artifact["source_window"],
            source["source_window"],
        )
        payload = compute_payload(source, self.root / "plan.json")
        self.assertEqual({"body"}, set(payload["component_hashes"]))

    def test_chat_locator_identity_mismatch_fails_closed(self):
        transcript = self.write("chat.txt", "message")
        artifact = {
            "schema_version": "byteworker-source-locators/v1",
            "source_type": "feishu_chat",
            "source_chat_id": "oc_other",
            "source_window": "2026-07-29 .. 2026-07-30",
            "anchors": [],
        }

        with self.assertRaises(SourceBundleError) as caught:
            build_feishu_chat_bundle(
                source_uid="oc_test",
                title="测试群",
                source_window=artifact["source_window"],
                transcript={"path": str(transcript)},
                locator_artifact=artifact,
            )
        self.assertEqual(
            "SOURCE_BUNDLE_IDENTITY_MISMATCH",
            caught.exception.code,
        )

    def test_minutes_preserves_segment_anchors_and_revision(self):
        transcript = self.write(
            "minutes.txt",
            "[00:12.000] 负责人：下周完成联调。\n",
        )
        bundle = build_feishu_minutes_bundle(
            source_uid="obcn_test",
            source_url="https://example.test/minutes/obcn_test",
            title="项目周会",
            revision="2026-07-29T11:00:00+08:00",
            transcript={"path": str(transcript)},
            anchors=[
                {
                    "anchor_id": "minutes:segment:12000",
                    "kind": "minutes_segment",
                    "precision": "exact",
                    "open_url": (
                        "https://example.test/minutes/obcn_test?t=12000"
                    ),
                    "locator": {
                        "minute_token": "obcn_test",
                        "start_ms": 12000,
                        "end_ms": 18000,
                    },
                    "quote": "负责人：下周完成联调。",
                }
            ],
        )
        value = bundle.to_dict()
        source = feishu_minutes_bundle_to_transaction_source(bundle)

        self.assertEqual(
            "minutes_segment",
            value["anchors"][0]["kind"],
        )
        self.assertEqual("body", value["anchors"][0]["component"])
        self.assertEqual(
            "2026-07-29T11:00:00+08:00",
            source["revision"],
        )
        self.assertEqual("feishu_minutes", source["type"])
        compute_payload(source, self.root / "plan.json")

    def test_web_preserves_source_url_body_and_section_anchor(self):
        body = self.write("article.md", "# Architecture\n\n正文。\n")
        bundle = build_web_bundle(
            source_uid="https://example.test/article",
            source_url="https://example.test/article",
            title="Architecture",
            body={"path": str(body)},
            anchors=[
                {
                    "anchor_id": "web:section:architecture",
                    "kind": "web_section",
                    "precision": "exact",
                    "open_url": "https://example.test/article#architecture",
                    "locator": {"heading": "Architecture"},
                }
            ],
            provider_metadata={"fetched_at": "2026-07-29T12:00:00+08:00"},
        )
        source = web_bundle_to_transaction_source(bundle)

        self.assertEqual("https://example.test/article", source["url"])
        self.assertEqual(
            str(body.resolve()),
            source["components"][0]["path"],
        )
        self.assertEqual("text/markdown", bundle.components[0].media_type)
        compute_payload(source, self.root / "plan.json")

    def test_local_markdown_keeps_original_path_and_local_anchor(self):
        local_file = self.write("local.md", "# 用户确认\n\n原始文本。\n")
        bundle = build_local_markdown_bundle(
            source_uid="direct-user:performance:example",
            title="用户确认材料",
            local_file={"path": str(local_file)},
            anchors=[
                {
                    "anchor_id": "local:span:1-3",
                    "kind": "local_span",
                    "precision": "exact",
                    "locator": {
                        "path": str(local_file),
                        "line_start": 1,
                        "line_end": 3,
                    },
                }
            ],
        )
        value = bundle.to_dict()
        source = local_markdown_bundle_to_transaction_source(bundle)

        self.assertEqual("", value["identity"]["source_url"])
        self.assertEqual(
            str(local_file.resolve()),
            value["provider_metadata"]["source_path"],
        )
        self.assertEqual(
            str(local_file.resolve()),
            source["components"][0]["path"],
        )
        self.assertEqual("local_md", source["type"])
        compute_payload(source, self.root / "plan.json")

    def test_source_specific_anchor_kind_is_enforced(self):
        body = self.write("article.md", "body")

        with self.assertRaises(SourceBundleError) as caught:
            build_web_bundle(
                source_uid="https://example.test/article",
                source_url="https://example.test/article",
                title="Article",
                body={"path": str(body)},
                anchors=[
                    {
                        "anchor_id": "chat:message:om_1",
                        "kind": "chat_message",
                        "precision": "exact",
                        "locator": {"message_id": "om_1"},
                    }
                ],
            )
        self.assertEqual(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            caught.exception.code,
        )

    def test_remote_text_sources_require_current_transaction_url(self):
        body = self.write("body.md", "body")

        for builder, body_argument in (
            (build_feishu_minutes_bundle, {"transcript": {"path": str(body)}}),
            (build_web_bundle, {"body": {"path": str(body)}}),
        ):
            with self.subTest(builder=builder.__name__):
                with self.assertRaises(SourceBundleError) as caught:
                    builder(
                        source_uid="source-1",
                        source_url="",
                        title="Title",
                        **body_argument,
                    )
                self.assertEqual(
                    "SOURCE_BUNDLE_ADAPTER_INVALID",
                    caught.exception.code,
                )


if __name__ == "__main__":
    unittest.main()
