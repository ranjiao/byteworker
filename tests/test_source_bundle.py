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

from sources import (  # noqa: E402
    BUNDLE_SCHEMA,
    SourceAdapterRegistry,
    SourceBundle,
    SourceBundleError,
    SourceRegistryError,
    build_feishu_document_bundle,
    build_meego_bundle,
    canonical_sha256,
    create_default_registry,
    load_source_bundle,
    validate_source_bundle,
)
from digest_txn import compute_payload  # noqa: E402


class SourceBundleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, name, value):
        path = self.root / name
        if isinstance(value, str):
            path.write_text(value, encoding="utf-8")
        else:
            path.write_text(
                json.dumps(value, ensure_ascii=False),
                encoding="utf-8",
            )
        return path

    def meego_capture(self):
        snapshot = {
            "schema_version": "byteworker-source-snapshot/v1",
            "source_type": "meego",
            "source_uid": "meego:proj:view",
            "coordinates": {"project_key": "proj", "view_id": "view"},
            "fields": ["name", "status"],
            "records": [
                {
                    "work_item_id": "1",
                    "name": "安全基座",
                    "status": "doing",
                    "updated_at": "2026-07-29T10:00:00+08:00",
                },
                {
                    "work_item_info": {
                        "work_item_id": "2",
                        "work_item_name": "审核链路",
                    }
                },
            ],
        }
        return {
            "schema_version": "byteworker-source-capture/v1",
            "capture_mode": "snapshot",
            "captured_at": "2026-07-29T11:00:00+08:00",
            "source_type": "meego",
            "source_uid": "meego:proj:view",
            "source_url": "https://project.feishu.cn/proj/view/view",
            "title": "安全需求视图",
            "coordinates": {"project_key": "proj", "view_id": "view"},
            "requested_fields": ["name", "status"],
            "pagination": {
                "complete": True,
                "item_count": 2,
                "auto_paginated": True,
            },
            "sanitization": {
                "removed_sensitive_query_parameters": 0,
            },
            "snapshot": snapshot,
            "content_hash": canonical_sha256(snapshot),
            "anchors": [
                {
                    "anchor_id": "workitem:1",
                    "kind": "meego_workitem",
                    "precision": "exact",
                    "locator": {
                        "project_key": "proj",
                        "view_id": "view",
                        "work_item_id": "1",
                    },
                    "open_url": "https://project.feishu.cn/proj/view/view",
                    "label": "安全基座",
                },
                {
                    "anchor_id": "workitem:2",
                    "kind": "meego_workitem",
                    "precision": "exact",
                    "locator": {
                        "project_key": "proj",
                        "view_id": "view",
                        "work_item_id": "2",
                    },
                    "open_url": "https://project.feishu.cn/proj/view/view",
                    "label": "审核链路",
                },
            ],
        }

    def test_meego_capture_builds_valid_bundle_without_mutating_capture(self):
        capture = self.meego_capture()
        before = copy.deepcopy(capture)
        capture_path = self.write("meego.json", capture)
        bundle = build_meego_bundle(capture, capture_path=capture_path)
        value = bundle.to_dict()

        self.assertEqual(before, capture)
        self.assertEqual(BUNDLE_SCHEMA, value["schema_version"])
        self.assertEqual("meego:proj:view", value["identity"]["source_uid"])
        self.assertEqual(capture["content_hash"], value["snapshot_hash"])
        self.assertIsNone(value["payload_hash"])
        self.assertEqual(
            ["1", "2"],
            [item["record_id"] for item in value["record_index"]],
        )
        self.assertEqual("审核链路", value["record_index"][1]["title"])
        self.assertEqual(
            {"project_key": "proj", "view_id": "view", "work_item_id": "1"},
            value["record_index"][0]["locator"],
        )
        self.assertEqual(
            {"name": "安全基座", "status": "doing"},
            value["record_index"][0]["fields"],
        )
        self.assertEqual("/snapshot", value["components"][0]["json_pointer"])
        self.assertEqual("complete", value["coverage"]["status"])
        source = create_default_registry().to_transaction_source(bundle)
        self.assertEqual("proj", source["project_key"])
        self.assertEqual("view", source["view_id"])
        self.assertEqual(["name", "status"], source["fields"])
        self.assertEqual("records", source["components"][0]["kind"])
        self.assertEqual(
            ["1", "2"],
            [item["record_id"] for item in source["record_index"]],
        )
        payload = compute_payload(source, self.root / "plan.json")
        self.assertEqual(capture["content_hash"], payload["component_hashes"]["snapshot"])

    def test_meego_rejects_incomplete_or_tampered_capture(self):
        capture = self.meego_capture()
        capture_path = self.write("meego.json", capture)
        capture["pagination"]["complete"] = False
        with self.assertRaises(SourceBundleError) as caught:
            build_meego_bundle(capture, capture_path=capture_path)
        self.assertEqual("SOURCE_BUNDLE_INCOMPLETE", caught.exception.code)

        capture = self.meego_capture()
        capture["snapshot"]["records"][0]["name"] = "tampered"
        with self.assertRaises(SourceBundleError) as caught:
            build_meego_bundle(capture, capture_path=capture_path)
        self.assertEqual("SOURCE_BUNDLE_HASH_MISMATCH", caught.exception.code)

    def test_reloaded_meego_bundle_revalidates_against_capture(self):
        capture = self.meego_capture()
        capture_path = self.write("meego-revalidate.json", capture)
        value = build_meego_bundle(
            capture,
            capture_path=capture_path,
        ).to_dict()
        value["identity"]["source_uid"] = "meego:other:view"
        loaded = SourceBundle.from_dict(value)

        with self.assertRaises(SourceBundleError) as caught:
            create_default_registry().to_transaction_source(loaded)
        self.assertEqual(
            "SOURCE_BUNDLE_PROVIDER_MISMATCH",
            caught.exception.code,
        )

    def test_feishu_document_keeps_typed_components_and_coverage(self):
        body = self.write("body.xml", "<p id=\"p1\">结论</p>")
        comments = self.write(
            "comments.json",
            {"coverage": {"status": "complete"}, "comments": []},
        )
        board = self.write("board.json", {"nodes": []})
        model = build_feishu_document_bundle(
            source_uid="doc-1",
            source_url="https://example.test/docx/doc-1",
            title="测试文档",
            revision="7",
            body={"path": str(body)},
            comments={
                "path": str(comments),
                "json_pointer": "/comments",
            },
            whiteboards=[
                {
                    "name": "whiteboard:wb1",
                    "path": str(board),
                    "uid": "wb1",
                }
            ],
            anchors=[
                {
                    "anchor_id": "doc:block:p1",
                    "kind": "doc_block",
                    "precision": "exact",
                    "locator": {"block_id": "p1"},
                    "open_url": "https://example.test/docx/doc-1",
                    "component": "body",
                }
            ],
        )
        bundle = model.to_dict()

        self.assertEqual(
            ["body", "comments", "whiteboard"],
            [item["kind"] for item in bundle["components"]],
        )
        self.assertEqual("complete", bundle["coverage"]["status"])
        self.assertIsNone(bundle["snapshot_hash"])
        self.assertIsNone(bundle["payload_hash"])
        self.assertNotIn("record_index", bundle)
        source = create_default_registry().to_transaction_source(model)
        self.assertEqual("complete", source["comments_status"])
        self.assertEqual("complete", source["whiteboards_status"])
        self.assertEqual("7", source["revision"])
        payload = compute_payload(source, self.root / "plan.json")
        self.assertEqual(
            {"body", "comments", "whiteboard:wb1"},
            set(payload["component_hashes"]),
        )

    def test_partial_component_coverage_must_match_overall_status(self):
        body = self.write("body.xml", "body")
        comments = self.write("comments.json", {"comments": []})
        bundle = build_feishu_document_bundle(
            source_uid="doc-1",
            source_url="https://example.test/docx/doc-1",
            title="测试文档",
            revision="7",
            body={"path": str(body)},
            comments={"path": str(comments), "coverage": "partial"},
        ).to_dict()
        self.assertEqual("partial", bundle["coverage"]["status"])

        bundle["coverage"]["status"] = "complete"
        with self.assertRaises(SourceBundleError):
            validate_source_bundle(bundle)

    def test_feishu_document_can_truthfully_omit_unavailable_comments(self):
        body = self.write("body.xml", "body")
        model = build_feishu_document_bundle(
            source_uid="doc-1",
            source_url="https://example.test/docx/doc-1",
            title="测试文档",
            revision="7",
            body={"path": str(body)},
            provider_metadata={"comments_status": "unavailable"},
        )
        bundle = model.to_dict()
        source = create_default_registry().to_transaction_source(model)

        self.assertEqual(["body"], [item["kind"] for item in bundle["components"]])
        self.assertEqual("unavailable", source["comments_status"])
        compute_payload(source, self.root / "plan.json")

    def test_rejects_credentials_anywhere_in_bundle(self):
        body = self.write("body.xml", "body")
        comments = self.write("comments.json", {"comments": []})
        with self.assertRaises(SourceBundleError) as caught:
            build_feishu_document_bundle(
                source_uid="doc-1",
                source_url="https://example.test/docx/doc-1",
                title="测试文档",
                revision="7",
                body={"path": str(body)},
                comments={"path": str(comments)},
                provider_metadata={"client_secret": "do-not-store"},
            )
        self.assertEqual(
            "SOURCE_BUNDLE_CONTAINS_CREDENTIAL",
            caught.exception.code,
        )

        with self.assertRaises(SourceBundleError) as caught:
            build_feishu_document_bundle(
                source_uid="doc-1",
                source_url=(
                    "https://example.test/docx/doc-1?"
                    "disposable_login_token=secret"
                ),
                title="测试文档",
                revision="7",
                body={"path": str(body)},
                comments={"path": str(comments)},
            )
        self.assertEqual(
            "SOURCE_BUNDLE_CONTAINS_CREDENTIAL",
            caught.exception.code,
        )

    def test_rejects_business_component_inside_skill_repository(self):
        with self.assertRaises(SourceBundleError) as caught:
            build_feishu_document_bundle(
                source_uid="doc-1",
                source_url="https://example.test/docx/doc-1",
                title="测试文档",
                revision="7",
                body={"path": str(ROOT / "README-business.xml")},
                comments={"path": str(self.write("comments.json", {}))},
            )
        self.assertEqual(
            "SOURCE_BUNDLE_PATH_IN_SKILL_REPO",
            caught.exception.code,
        )

    def test_load_bundle_validates_file_and_rejects_bundle_in_skill_repo(self):
        body = self.write("body.xml", "body")
        comments = self.write("comments.json", {})
        value = build_feishu_document_bundle(
            source_uid="doc-1",
            source_url="https://example.test/docx/doc-1",
            title="测试文档",
            revision="7",
            body={"path": str(body)},
            comments={"path": str(comments)},
        ).to_dict()
        bundle_path = self.write("bundle.json", value)
        self.assertEqual("doc-1", load_source_bundle(bundle_path).identity.source_uid)
        with self.assertRaises(SourceBundleError) as caught:
            load_source_bundle(ROOT / "bundle.json")
        self.assertEqual(
            "SOURCE_BUNDLE_PATH_IN_SKILL_REPO",
            caught.exception.code,
        )

    def test_rejects_invalid_anchor_and_hash_semantics(self):
        body = self.write("body.xml", "body")
        comments = self.write("comments.json", {})
        bundle = build_feishu_document_bundle(
            source_uid="doc-1",
            source_url="https://example.test/docx/doc-1",
            title="测试文档",
            revision="7",
            body={"path": str(body)},
            comments={"path": str(comments)},
        ).to_dict()
        bundle["anchors"] = [
            {
                "anchor_id": "invalid anchor",
                "kind": "doc_block",
                "precision": "exact",
                "locator": {"block_id": "p1"},
            }
        ]
        with self.assertRaises(SourceBundleError):
            validate_source_bundle(bundle)

        bundle["anchors"] = []
        bundle["snapshot_hash"] = "sha256:not-a-real-hash"
        with self.assertRaises(SourceBundleError):
            validate_source_bundle(bundle)

    def test_default_registry_exposes_capabilities_and_rejects_duplicates(self):
        registry = create_default_registry()
        self.assertEqual(
            (
                "aeolus",
                "feishu_base",
                "feishu_chat",
                "feishu_doc",
                "feishu_minutes",
                "local_md",
                "meego",
                "web",
            ),
            registry.source_types(),
        )
        self.assertTrue(registry.capabilities("meego").stable_record_ids)
        self.assertTrue(registry.capabilities("feishu_base").record_index)
        self.assertTrue(registry.capabilities("aeolus").record_index)
        self.assertFalse(registry.capabilities("feishu_doc").record_index)
        with self.assertRaises(SourceRegistryError):
            registry.register(registry.get("meego"))
        with self.assertRaises(SourceRegistryError):
            SourceAdapterRegistry().get("missing")

    def test_registry_distinguishes_request_shape_from_adapter_type_bug(self):
        class BrokenAdapter:
            source_type = "broken"
            capabilities = create_default_registry().capabilities("local_md")

            @staticmethod
            def request_builder(*, value):
                return value

            def build_bundle(self, **kwargs):
                raise TypeError("implementation regression")

            def validate_bundle(self, bundle):
                return None

            def to_transaction_source(self, bundle):
                return {}

        registry = SourceAdapterRegistry()
        registry.register(BrokenAdapter())
        with self.assertRaises(SourceRegistryError) as caught:
            registry.build_bundle("broken", unexpected=True)
        self.assertEqual(
            "SOURCE_BUNDLE_REQUEST_INVALID",
            caught.exception.code,
        )
        with self.assertRaises(SourceRegistryError) as caught:
            registry.build_bundle("broken", value=True)
        self.assertEqual(
            "SOURCE_ADAPTER_INTERNAL_ERROR",
            caught.exception.code,
        )


if __name__ == "__main__":
    unittest.main()
