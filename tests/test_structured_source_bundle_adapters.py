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
from sources.adapters.aeolus import (  # noqa: E402
    AeolusCaptureAdapter,
    aeolus_bundle_to_transaction_source,
    build_aeolus_bundle,
)
from sources.adapters.feishu_base import (  # noqa: E402
    FeishuBaseCaptureAdapter,
    build_feishu_base_bundle,
    feishu_base_bundle_to_transaction_source,
)
from sources import create_default_registry  # noqa: E402
from sources.models import (  # noqa: E402
    SourceBundle,
    SourceBundleError,
    canonical_sha256,
)


class StructuredSourceBundleAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def write_capture(self, name, capture):
        path = self.root / name
        path.write_text(
            json.dumps(capture, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def base_capture(self):
        coordinates = {
            "base_token": "bas1",
            "table_id": "tbl1",
            "view_id": "vew1",
        }
        snapshot = {
            "schema_version": "byteworker-source-snapshot/v1",
            "source_type": "feishu_base",
            "source_uid": "feishu_base:bas1:tbl1:vew1",
            "coordinates": coordinates,
            "fields": [
                {"field_id": "fld_name", "name": "需求名称", "type": "text"},
                {"field_id": "fld_status", "name": "状态", "type": "text"},
            ],
            "records": [
                {
                    "record_id": "rec2",
                    "fields": {"需求名称": "审核链路", "状态": "待评审"},
                },
                {
                    "record_id": "rec1",
                    "fields": {"需求名称": "安全基座", "状态": "进行中"},
                    "updated_at": "2026-07-29T10:00:00+08:00",
                },
            ],
        }
        return {
            "schema_version": "byteworker-source-capture/v1",
            "capture_mode": "snapshot",
            "captured_at": "2026-07-29T11:00:00+08:00",
            "source_type": "feishu_base",
            "source_uid": "feishu_base:bas1:tbl1:vew1",
            "source_url": (
                "https://example.test/base/bas1?table=tbl1&view=vew1"
            ),
            "title": "需求库 / 本周更新",
            "coordinates": coordinates,
            "requested_fields": ["fld_name", "fld_status"],
            "pagination": {"complete": True, "pages": 1, "item_count": 2},
            "sanitization": {"removed_sensitive_query_parameters": 0},
            "snapshot": snapshot,
            "content_hash": canonical_sha256(snapshot),
            "anchors": [
                {
                    "anchor_id": "record:rec2",
                    "kind": "base_record",
                    "precision": "exact",
                    "locator": {**coordinates, "record_id": "rec2"},
                    "open_url": (
                        "https://example.test/base/bas1?table=tbl1&view=vew1"
                    ),
                    "label": "审核链路",
                },
                {
                    "anchor_id": "record:rec1",
                    "kind": "base_record",
                    "precision": "exact",
                    "locator": {**coordinates, "record_id": "rec1"},
                    "open_url": (
                        "https://example.test/base/bas1?table=tbl1&view=vew1"
                    ),
                    "label": "安全基座",
                    "source_time": "2026-07-29T10:00:00+08:00",
                },
            ],
        }

    def aeolus_capture(self, *, with_profile=True):
        coordinates = {
            "region": "cn",
            "app_id": 101,
            "dashboard_id": 202,
            "sheet_id": 303,
        }
        filters = [
            {
                "name": "区域",
                "dimMetId": 602,
                "op": "in",
                "val": ["CN"],
            }
        ]
        record = {
            "record_id": "report:401",
            "report_id": 401,
            "name": "示例指标卡片",
            "display_type": "measure_card",
            "dataset_id": 501,
            "columns": ["满足率"],
            "effective_filters": filters,
            "row_count": 1,
            "rows": [{"满足率": "0.96"}],
            "freshness": {
                "status": "unknown",
                "reason": "provider 未返回更新时间",
            },
        }
        snapshot = {
            "schema_version": "byteworker-source-snapshot/v1",
            "source_type": "aeolus",
            "source_uid": "aeolus:cn:101:202:303",
            "coordinates": coordinates,
            "selector": {
                "kind": "dashboard_sheet",
                "filter_mode": "merge",
                "report_ids": [401],
                "where_filters": filters,
            },
            "public_filters": [],
            "records": [record],
        }
        capture = {
            "schema_version": "byteworker-source-capture/v1",
            "capture_mode": "snapshot",
            "captured_at": "2026-07-29T11:00:00+08:00",
            "source_type": "aeolus",
            "source_uid": "aeolus:cn:101:202:303",
            "source_url": (
                "https://data.bytedance.net/aeolus/pages/dashboard/202"
                "?appId=101&sheetId=303"
            ),
            "title": "示例指标看板",
            "coordinates": coordinates,
            "requested_report_ids": [401],
            "filter_mode": "merge",
            "where_filters": filters,
            "pagination": {
                "complete": True,
                "item_count": 1,
                "row_count": 1,
                "bounded_rows_per_report": 1000,
            },
            "sanitization": {"removed_sensitive_query_parameters": 0},
            "snapshot": snapshot,
            "content_hash": canonical_sha256(snapshot),
            "anchors": [
                {
                    "anchor_id": "aeolus:report:401",
                    "kind": "aeolus_report",
                    "precision": "exact",
                    "locator": {
                        **coordinates,
                        "report_id": 401,
                        "dataset_id": 501,
                        "effective_filters": filters,
                    },
                    "open_url": (
                        "https://data.bytedance.net/aeolus/pages/dashboard/202"
                        "?appId=101&sheetId=303"
                    ),
                    "label": "示例指标卡片",
                }
            ],
        }
        if with_profile:
            capture["source_profile"] = {
                "source_uid": "aeolus:cn:101:202:303",
                "revision": "sha256:" + "a" * 64,
                "path": "sources/aeolus-example.json",
            }
        return capture

    def test_base_capture_v1_builds_stable_bundle_and_transaction_source(self):
        capture = self.base_capture()
        original = copy.deepcopy(capture)
        path = self.write_capture("base.json", capture)
        bundle = build_feishu_base_bundle(capture, capture_path=path)
        value = bundle.to_dict()

        self.assertEqual(original, capture)
        self.assertEqual(capture["content_hash"], value["snapshot_hash"])
        self.assertEqual(
            ["rec1", "rec2"],
            [item["record_id"] for item in value["record_index"]],
        )
        self.assertEqual("安全基座", value["record_index"][0]["title"])
        self.assertEqual(
            "record:rec1", value["record_index"][0]["anchor_id"]
        )
        self.assertEqual(
            ["record:rec1", "record:rec2"],
            [item["anchor_id"] for item in value["anchors"]],
        )
        self.assertTrue(FeishuBaseCaptureAdapter.capabilities.record_index)

        source = feishu_base_bundle_to_transaction_source(bundle)
        self.assertEqual("bas1", source["base_token"])
        self.assertEqual(["fld_name", "fld_status"], source["fields"])
        payload = compute_payload(source, self.root / "plan.json")
        self.assertEqual(
            capture["content_hash"], payload["component_hashes"]["snapshot"]
        )
        loaded = build_feishu_base_bundle(capture_path=path)
        self.assertEqual(bundle.snapshot_hash, loaded.snapshot_hash)

    def test_base_adapter_fails_closed_on_incomplete_or_wrong_anchor(self):
        capture = self.base_capture()
        path = self.write_capture("base.json", capture)
        capture["pagination"]["complete"] = False
        with self.assertRaises(SourceBundleError) as caught:
            build_feishu_base_bundle(capture, capture_path=path)
        self.assertEqual("SOURCE_BUNDLE_INCOMPLETE", caught.exception.code)

        capture = self.base_capture()
        capture["anchors"][0]["locator"]["record_id"] = "rec-other"
        capture["content_hash"] = canonical_sha256(capture["snapshot"])
        with self.assertRaises(SourceBundleError) as caught:
            build_feishu_base_bundle(capture, capture_path=path)
        self.assertEqual(
            "SOURCE_BUNDLE_IDENTITY_MISMATCH", caught.exception.code
        )

    def test_reloaded_base_bundle_revalidates_provider_derived_fields(self):
        capture = self.base_capture()
        path = self.write_capture("base-revalidate.json", capture)
        value = build_feishu_base_bundle(
            capture,
            capture_path=path,
        ).to_dict()
        registry = create_default_registry()

        mutations = (
            lambda item: item["identity"].update(
                {"source_uid": "feishu_base:other:table:view"}
            ),
            lambda item: item["components"][0].update({"kind": "body"}),
            lambda item: item["provider_metadata"]["coordinates"].update(
                {"view_id": "other"}
            ),
            lambda item: item["record_index"][0]["fields"].update(
                {"状态": "tampered"}
            ),
            lambda item: item.update({"snapshot_hash": None}),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                tampered = copy.deepcopy(value)
                mutate(tampered)
                loaded = SourceBundle.from_dict(tampered)
                with self.assertRaises(SourceBundleError):
                    registry.to_transaction_source(loaded)

    def test_aeolus_capture_v1_builds_queryable_bundle_and_transaction_source(self):
        capture = self.aeolus_capture()
        original = copy.deepcopy(capture)
        path = self.write_capture("aeolus.json", capture)
        bundle = build_aeolus_bundle(capture, capture_path=path)
        value = bundle.to_dict()

        self.assertEqual(original, capture)
        self.assertEqual(capture["content_hash"], value["snapshot_hash"])
        self.assertEqual("report:401", value["record_index"][0]["record_id"])
        self.assertEqual(
            "示例指标卡片", value["record_index"][0]["title"]
        )
        self.assertEqual(
            [{"满足率": "0.96"}],
            value["record_index"][0]["fields"]["rows"],
        )
        self.assertEqual(
            "aeolus:report:401", value["record_index"][0]["anchor_id"]
        )
        self.assertTrue(AeolusCaptureAdapter.capabilities.incremental_diff)

        source = aeolus_bundle_to_transaction_source(bundle)
        self.assertEqual("sources/aeolus-example.json", source["profile_path"])
        self.assertEqual([401], source["report_ids"])
        payload = compute_payload(source, self.root / "plan.json")
        self.assertEqual(
            capture["content_hash"], payload["component_hashes"]["snapshot"]
        )
        loaded = build_aeolus_bundle(capture_path=path)
        self.assertEqual(bundle.snapshot_hash, loaded.snapshot_hash)

    def test_aeolus_adapter_fails_closed_on_selector_or_profile_mismatch(self):
        capture = self.aeolus_capture()
        path = self.write_capture("aeolus.json", capture)
        capture["snapshot"]["selector"]["filter_mode"] = "dashboard"
        capture["content_hash"] = canonical_sha256(capture["snapshot"])
        with self.assertRaises(SourceBundleError) as caught:
            build_aeolus_bundle(capture, capture_path=path)
        self.assertEqual(
            "SOURCE_BUNDLE_IDENTITY_MISMATCH", caught.exception.code
        )

        capture = self.aeolus_capture()
        capture["source_profile"]["source_uid"] = "aeolus:cn:1:2:3"
        with self.assertRaises(SourceBundleError) as caught:
            build_aeolus_bundle(capture, capture_path=path)
        self.assertEqual(
            "SOURCE_BUNDLE_IDENTITY_MISMATCH", caught.exception.code
        )

    def test_aeolus_bundle_without_profile_fails_before_handoff(self):
        capture = self.aeolus_capture(with_profile=False)
        path = self.write_capture("aeolus-no-profile.json", capture)
        with self.assertRaises(SourceBundleError) as caught:
            build_aeolus_bundle(capture, capture_path=path)
        self.assertEqual("SOURCE_BUNDLE_ADAPTER_INVALID", caught.exception.code)

    def test_reloaded_aeolus_bundle_revalidates_against_capture(self):
        capture = self.aeolus_capture()
        path = self.write_capture("aeolus-revalidate.json", capture)
        value = build_aeolus_bundle(capture, capture_path=path).to_dict()
        value["record_index"][0]["locator"]["report_id"] = 999
        loaded = SourceBundle.from_dict(value)

        with self.assertRaises(SourceBundleError) as caught:
            create_default_registry().to_transaction_source(loaded)
        self.assertEqual(
            "SOURCE_BUNDLE_PROVIDER_MISMATCH",
            caught.exception.code,
        )


if __name__ == "__main__":
    unittest.main()
