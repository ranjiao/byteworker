import json
import sys
import tempfile
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from source_capture import (  # noqa: E402
    BASE_READ_SCOPES,
    CliResponse,
    SourceCaptureError,
    _classify_cli_error,
    _process_failure,
    _unwrap_response,
    aeolus_auth_status,
    base_auth_status,
    build_aeolus_profile,
    capture_aeolus,
    capture_aeolus_from_profile,
    capture_base,
    capture_meego,
    diff_captures,
    inspect_aeolus,
    inspect_base,
    inspect_meego,
    meego_auth_status,
    write_capture,
)


class FakeRunner:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def run(self, args, *, provider):
        values = list(args)
        self.calls.append((values, provider))
        result = self.handler(values)
        if isinstance(result, CliResponse):
            return result
        return CliResponse(data=result, meta={}, raw=result)


def value_after(args, flag, default=""):
    try:
        return args[args.index(flag) + 1]
    except ValueError:
        return default


class FakeAeolusClient:
    def __init__(
        self,
        *,
        auth=None,
        query_rows=None,
        query_columns=None,
    ):
        self.auth = auth or {
            "configured": True,
            "authenticated": True,
            "authorized": True,
            "ready": True,
            "auth_type": "bytecloud_jwt",
        }
        self.query_rows = query_rows
        self.query_columns = query_columns
        self.calls = []

    def auth_status(self):
        self.calls.append(("auth_status", {}))
        return self.auth

    def resolve_dashboard(self, url):
        self.calls.append(("resolve_dashboard", {"url": url}))
        return (
            {
                "region": "cn",
                "app_id": 101,
                "dashboard_id": 202,
                "sheet_id": 303,
            },
            {
                "region": "cn",
                "urlType": "dashboard",
                "appId": 101,
                "dashboardId": 202,
                "sheetId": 303,
                "dashboardName": "Example dashboard",
                "reports": [
                    {
                        "reportId": 401,
                        "datasetIds": [501],
                        "name": "交付卡片",
                        "displayType": "measure_card",
                        "statusCode": "resolved",
                    }
                ],
            },
        )

    def get_sheet(self, *, coordinates, url):
        self.calls.append(
            ("get_sheet", {"coordinates": coordinates, "url": url})
        )
        return {
            "content": {
                "componentTree": {
                    "children": [
                        {
                            "componentName": "filter",
                            "props": {
                                "chartIDs": [401],
                                "fields": [
                                    {
                                        "dataSetId": 501,
                                        "dimMetId": 601,
                                    }
                                ],
                                "filter": {
                                    "op": "in",
                                    "val": ["示例部门"],
                                },
                                "filterKey": "department",
                                "invisible": True,
                                "name": "部门名称",
                            },
                        }
                    ]
                }
            }
        }

    def get_dataset_fields(self, dataset_id):
        self.calls.append(
            ("get_dataset_fields", {"dataset_id": dataset_id})
        )
        return {
            "datasetId": "501",
            "datasetName": "Example dataset",
            "dimensions": [
                {
                    "id": 601,
                    "name": "一级业务部门",
                    "dataTypeName": "string",
                }
            ],
            "metrics": [],
        }

    def query_report(self, **kwargs):
        self.calls.append(("query_report", kwargs))
        return {
            "requestId": "request-1",
            "columns": self.query_columns
            or ["预期按预算交付", "合计交付", "满足率"],
            "rows": self.query_rows
            if self.query_rows is not None
            else [[[[[{"a": "10", "b": "12", "c": "0.8"}]]]]],
        }


def aeolus_client(query_rows=None, query_columns=None):
    return FakeAeolusClient(
        query_rows=query_rows,
        query_columns=query_columns,
    )


class AeolusCaptureTests(unittest.TestCase):
    def test_profile_owns_report_filter_and_routine_configuration(self):
        client = aeolus_client()
        profile = build_aeolus_profile(
            client=client,
            url="https://data.bytedance.net/aeolus/pages/dashboard/202"
            "?appId=101&sheetId=303",
            report_ids=[401],
            where_filters=[
                {
                    "name": "一级业务部门",
                    "dimMetId": 601,
                    "op": "in",
                    "val": ["另一个部门"],
                }
            ],
            filter_mode="explicit",
            max_items=50,
            routine="weekly",
        )
        self.assertEqual(
            [401],
            profile["capture"]["report_selector"]["report_ids"],
        )
        self.assertEqual("explicit", profile["capture"]["filters"]["mode"])
        self.assertEqual("weekly", profile["routine"]["cadence"])

        result = capture_aeolus_from_profile(client=client, profile=profile)
        query = [item for item in client.calls if item[0] == "query_report"][-1][1]
        self.assertEqual(
            ["另一个部门"],
            query["where_filters"][0]["val"],
        )
        self.assertEqual(50, query["limit"])
        self.assertEqual(profile["source_uid"], result["source_profile"]["source_uid"])

    def test_auth_status_requires_ready_bytecloud_user(self):
        ready = aeolus_auth_status(client=aeolus_client())
        self.assertTrue(ready["ready"])
        self.assertEqual("bytecloud_jwt", ready["auth_type"])

        logged_out = aeolus_auth_status(
            client=FakeAeolusClient(
                auth={
                    "configured": False,
                    "authenticated": False,
                    "authorized": False,
                    "ready": False,
                    "auth_type": None,
                }
            )
        )
        self.assertFalse(logged_out["ready"])
        self.assertEqual(
            "configure_credentials",
            logged_out["action"]["kind"],
        )

    def test_inspect_resolves_hidden_dashboard_filter_to_dataset_name(self):
        result = inspect_aeolus(
            client=aeolus_client(),
            url="https://data.bytedance.net/aeolus/pages/dashboard/202"
            "?appId=101&sheetId=303",
        )
        self.assertEqual(
            "aeolus:cn:101:202:303",
            result["source_uid"],
        )
        self.assertEqual(1, result["active_public_filter_count"])
        self.assertEqual("一级业务部门", result["public_filters"][0]["name"])
        self.assertTrue(result["public_filters"][0]["hidden"])

    def test_capture_replays_filter_and_normalizes_nested_card(self):
        client = aeolus_client()
        result = capture_aeolus(
            client=client,
            url="https://data.bytedance.net/aeolus/pages/dashboard/202"
            "?appId=101&sheetId=303",
        )
        report = result["snapshot"]["records"][0]
        self.assertEqual("report:401", report["record_id"])
        self.assertEqual(
            {
                "预期按预算交付": "10",
                "合计交付": "12",
                "满足率": "0.8",
            },
            report["rows"][0],
        )
        query_args = next(
            values
            for name, values in client.calls
            if name == "query_report"
        )
        where = query_args["where_filters"][0]
        self.assertEqual("一级业务部门", where["name"])
        self.assertEqual(["示例部门"], where["val"])
        self.assertEqual("aeolus_report", result["anchors"][0]["kind"])
        self.assertEqual("unknown", report["freshness"]["status"])

    def test_long_form_chart_is_pivoted_without_helper_columns(self):
        rows = [
            [
                [
                    [
                        [
                            {
                                "10001": "指标A",
                                "10002": "1",
                                "10003": "metric-a",
                                "20001": "指标A",
                                "department": "部门",
                                "month": "7月",
                                "metric-a": "1",
                            },
                            {
                                "10001": "指标B",
                                "10002": "2",
                                "10003": "metric-b",
                                "20001": "指标B",
                                "department": "部门",
                                "month": "7月",
                                "metric-b": "2",
                            },
                        ]
                    ]
                ]
            ]
        ]
        result = capture_aeolus(
            client=aeolus_client(
                query_rows=rows,
                query_columns=["部门", "月份", "指标A", "指标B"],
            ),
            url="https://data.bytedance.net/aeolus/pages/dashboard/202"
            "?appId=101&sheetId=303",
        )
        self.assertEqual(
            [{"部门": "部门", "月份": "7月", "指标A": "1", "指标B": "2"}],
            result["snapshot"]["records"][0]["rows"],
        )

    def test_unmappable_nested_shape_fails_closed(self):
        with self.assertRaises(SourceCaptureError) as raised:
            capture_aeolus(
                client=aeolus_client(
                    query_rows=[[[[[{"only": "one"}]]]]],
                    query_columns=["A", "B"],
                ),
                url="https://data.bytedance.net/aeolus/pages/dashboard/202"
                "?appId=101&sheetId=303",
            )
        self.assertEqual(
            "SOURCE_AEOLUS_NORMALIZATION_ERROR",
            raised.exception.code,
        )

    def test_explicit_filters_require_explicit_mode(self):
        with self.assertRaisesRegex(SourceCaptureError, "不接受"):
            capture_aeolus(
                client=aeolus_client(),
                url="https://data.bytedance.net/aeolus/pages/dashboard/202"
                "?appId=101&sheetId=303",
                where_filters=[
                    {
                        "name": "区域",
                        "dimMetId": 100,
                        "op": "in",
                        "val": ["CN"],
                    }
                ],
            )


class MeegoCaptureTests(unittest.TestCase):
    def runner(self, records, *, meta=None, pagination=None):
        def handle(args):
            if args[:2] == ["auth", "status"]:
                return {"authenticated": True, "host": "project.feishu.cn"}
            self.assertIn("view", args)
            data = {
                "list": list(records),
                "pagination": pagination or {"has_more": False},
            }
            return CliResponse(data=data, meta=meta or {}, raw=data)

        return FakeRunner(handle)

    def test_capture_is_sorted_and_hash_is_order_independent(self):
        records = [
            {"work_item_id": "2", "name": "B", "updated_at": "2026-07-29"},
            {"work_item_id": "1", "name": "A", "updated_at": "2026-07-28"},
        ]
        first = capture_meego(
            runner=self.runner(records),
            project_key="proj",
            view_id="view",
            fields=["name", "updated_at"],
        )
        second = capture_meego(
            runner=self.runner(list(reversed(records))),
            project_key="proj",
            view_id="view",
            fields=["updated_at", "name", "name"],
        )
        self.assertEqual(first["content_hash"], second["content_hash"])
        self.assertEqual(
            ["1", "2"],
            [item["work_item_id"] for item in first["snapshot"]["records"]],
        )
        self.assertEqual("meego_workitem", first["anchors"][0]["kind"])
        self.assertTrue(first["pagination"]["complete"])

    def test_sensitive_query_credentials_are_removed_before_hashing(self):
        result = capture_meego(
            runner=self.runner(
                [
                    {
                        "work_item_id": "1",
                        "name": "A",
                        "wiki": (
                            "https://example.test/doc?foo=bar&"
                            "disposable_login_token=top-secret#section"
                        ),
                    }
                ]
            ),
            project_key="proj",
            view_id="view",
            fields=["name", "wiki"],
        )
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("top-secret", serialized)
        self.assertNotIn("disposable_login_token", serialized)
        self.assertIn("foo=bar#section", serialized)
        self.assertEqual(
            1,
            result["sanitization"]["removed_sensitive_query_parameters"],
        )

    def test_truncated_auto_pagination_fails_closed(self):
        with self.assertRaisesRegex(SourceCaptureError, "未完成"):
            capture_meego(
                runner=self.runner(
                    [{"work_item_id": "1"}],
                    meta={"truncated": True},
                ),
                project_key="proj",
                view_id="view",
                fields=["name"],
            )

    def test_grouped_view_response_selects_nested_workitems(self):
        grouped = {
            "list": [
                {
                    "group_id": "g1",
                    "work_items": [
                        {"work_item_id": "2", "name": "B"},
                        {"work_item_id": "1", "name": "A"},
                    ],
                }
            ],
            "pagination": {"has_more": False},
        }

        def handle(args):
            if args[:2] == ["auth", "status"]:
                return {"authenticated": True}
            return grouped

        result = capture_meego(
            runner=FakeRunner(handle),
            project_key="proj",
            view_id="view",
            fields=["name"],
        )
        self.assertEqual(
            ["1", "2"],
            [item["work_item_id"] for item in result["snapshot"]["records"]],
        )

    def test_nested_work_item_info_provides_stable_id(self):
        records = [
            {
                "work_item_info": {
                    "work_item_id": "2",
                    "work_item_name": "B",
                }
            },
            {
                "work_item_info": {
                    "work_item_id": "1",
                    "work_item_name": "A",
                }
            },
        ]
        result = capture_meego(
            runner=self.runner(records),
            project_key="proj",
            view_id="view",
            fields=["name"],
        )
        self.assertEqual(
            ["1", "2"],
            [
                item["work_item_info"]["work_item_id"]
                for item in result["snapshot"]["records"]
            ],
        )

    def test_fields_are_required_before_business_read(self):
        runner = self.runner([])
        with self.assertRaisesRegex(SourceCaptureError, "至少需要一个"):
            capture_meego(
                runner=runner,
                project_key="proj",
                view_id="view",
                fields=[],
            )
        self.assertEqual([], runner.calls)

    def test_inspect_decodes_url_and_resolves_authoritative_project_key(self):
        def handle(args):
            if args[:2] == ["url", "decode"]:
                return {
                    "url_kind": "view_story",
                    "simple_name": "demo",
                    "view_id": "view1",
                }
            if args[:2] == ["auth", "status"]:
                return {"authenticated": True}
            if "project" in args and "search" in args:
                return {"list": [{"project_key": "project1"}]}
            if "meta-fields" in args:
                return {
                    "list": [
                        {
                            "field_key": "name",
                            "field_name": "名称",
                            "field_type": "_name",
                        }
                    ]
                }
            return {
                "view_name": "本周需求",
                "list": [{"work_item_id": "1"}],
                "pagination": {"has_more": False, "total": 1},
            }

        runner = FakeRunner(handle)
        result = inspect_meego(
            runner=runner,
            url="https://example.test/demo/story/view1",
        )
        self.assertEqual("meego:project1:view1", result["source_uid"])
        self.assertEqual("本周需求", result["title"])
        self.assertEqual(1, result["sample_item_count"])
        self.assertEqual("story", result["work_item_type"])
        self.assertEqual(1, result["field_count"])
        view_call = next(
            args for args, _ in runner.calls
            if "view" in args and "get" in args
        )
        self.assertEqual("project1", value_after(view_call, "--project-key"))
        self.assertEqual("view1", value_after(view_call, "--view-id"))

    def test_meego_uses_required_flags_and_real_response_key(self):
        def handle(args):
            if args[:2] == ["auth", "status"]:
                return {"authenticated": True}
            if "meta-fields" in args:
                return {"list": []}
            self.assertEqual("proj", value_after(args, "--project-key"))
            self.assertEqual("view", value_after(args, "--view-id"))
            self.assertIn("--fields", args)
            return {
                "work_item_list": [
                    {
                        "work_item_attribute": {
                            "work_item_id": "1",
                            "work_item_name": "A",
                        }
                    }
                ],
                "pagination": {"has_more": False, "total": 1},
            }

        runner = FakeRunner(handle)
        result = inspect_meego(
            runner=runner,
            project_key="proj",
            view_id="view",
        )
        self.assertEqual(1, result["sample_item_count"])

    def test_cross_project_view_url_is_rejected_in_first_version(self):
        runner = FakeRunner(
            lambda args: {
                "url_kind": "view_multi_project",
                "view_id": "view1",
            }
        )
        with self.assertRaisesRegex(SourceCaptureError, "第一版只支持"):
            inspect_meego(
                runner=runner,
                url="https://example.test/multi-project/view1",
            )
        self.assertEqual(1, len(runner.calls))


class BaseCaptureTests(unittest.TestCase):
    def runner(self, record_pages):
        fields = [
            {"field_id": "fld_name", "name": "Name", "type": "text"},
            {"field_id": "fld_status", "name": "Status", "type": "single_select"},
        ]

        def handle(args):
            if args[:2] == ["auth", "status"]:
                return {
                    "identity": "user",
                    "verified": True,
                    "identities": {
                        "user": {
                            "status": "ready",
                            "available": True,
                            "verified": True,
                            "tokenStatus": "valid",
                            "scope": " ".join(BASE_READ_SCOPES),
                        }
                    },
                }
            command = args[1]
            if command == "+base-get":
                return {"name": "需求库"}
            if command == "+table-get":
                return {"name": "需求"}
            if command == "+view-get":
                return {"name": "本周更新"}
            if command == "+field-list":
                return {"fields": fields, "has_more": False, "total": len(fields)}
            if command == "+record-list":
                offset = int(value_after(args, "--offset", "0"))
                return record_pages[offset]
            raise AssertionError(f"unexpected command: {args}")

        return FakeRunner(handle)

    def test_capture_serializes_all_pages_and_sorts_records(self):
        runner = self.runner(
            {
                0: {
                    "records": [
                        {"record_id": "rec2", "fields": {"Name": "B"}},
                        {"record_id": "rec3", "fields": {"Name": "C"}},
                    ],
                    "has_more": True,
                    "total": 3,
                },
                2: {
                    "records": [
                        {"record_id": "rec1", "fields": {"Name": "A"}}
                    ],
                    "has_more": False,
                    "total": 3,
                },
            }
        )
        capture = capture_base(
            runner=runner,
            base_token="bas1",
            table_id="tbl1",
            view_id="vew1",
            fields=["fld_name"],
        )
        self.assertEqual(2, capture["pagination"]["pages"])
        self.assertEqual(
            ["rec1", "rec2", "rec3"],
            [item["record_id"] for item in capture["snapshot"]["records"]],
        )
        self.assertEqual("base_record", capture["anchors"][0]["kind"])
        self.assertEqual(
            [{"field_id": "fld_name", "name": "Name", "type": "text"}],
            capture["snapshot"]["fields"],
        )
        offsets = [
            int(value_after(args, "--offset", "0"))
            for args, _ in runner.calls
            if len(args) > 1 and args[1] == "+record-list"
        ]
        self.assertEqual([0, 2], offsets)

    def test_inspect_resolves_url_before_reading_metadata(self):
        original = self.runner({})

        def handle(args):
            if args[1] == "+url-resolve":
                return {
                    "base_token": "bas1",
                    "table_id": "tbl1",
                    "view_id": "vew1",
                }
            return original.handler(args)

        runner = FakeRunner(handle)
        result = inspect_base(
            runner=runner,
            url="https://example.test/base/bas1?table=tbl1&view=vew1",
        )
        self.assertEqual(
            "feishu_base:bas1:tbl1:vew1",
            result["source_uid"],
        )
        self.assertEqual(2, result["field_count"])

    def test_empty_page_with_has_more_fails_closed(self):
        runner = self.runner(
            {
                0: {
                    "records": [],
                    "has_more": True,
                    "total": 1,
                }
            }
        )
        with self.assertRaisesRegex(SourceCaptureError, "空页"):
            capture_base(
                runner=runner,
                base_token="bas1",
                table_id="tbl1",
                view_id="vew1",
                fields=["Name"],
            )

    def test_unknown_field_is_rejected_before_record_read(self):
        runner = self.runner({})
        with self.assertRaisesRegex(SourceCaptureError, "不存在字段"):
            capture_base(
                runner=runner,
                base_token="bas1",
                table_id="tbl1",
                view_id="vew1",
                fields=["Missing"],
            )
        commands = [args[1] for args, _ in runner.calls]
        self.assertNotIn("+record-list", commands)


class CaptureOutputTests(unittest.TestCase):
    def test_meego_auth_status_is_actionable_without_starting_login(self):
        runner = FakeRunner(
            lambda args: {
                "authenticated": False,
                "host": None,
                "reason": "no local token",
            }
        )
        status = meego_auth_status(
            runner=runner,
            host="project.feishu.cn",
        )
        self.assertFalse(status["ready"])
        self.assertEqual("login", status["action"]["kind"])
        self.assertIn(
            "--host project.feishu.cn",
            status["action"]["command"],
        )
        self.assertEqual(1, len(runner.calls))

    def test_base_auth_status_reports_combined_minimum_scopes(self):
        runner = FakeRunner(
            lambda args: {
                "identity": "user",
                "verified": True,
                "identities": {
                    "user": {
                        "status": "ready",
                        "available": True,
                        "verified": True,
                        "tokenStatus": "valid",
                        "scope": "base:app:read",
                    }
                },
            }
        )
        status = base_auth_status(runner=runner)
        self.assertTrue(status["authenticated"])
        self.assertFalse(status["ready"])
        self.assertEqual(
            sorted(set(BASE_READ_SCOPES) - {"base:app:read"}),
            status["missing_scopes"],
        )
        for scope in BASE_READ_SCOPES:
            self.assertIn(scope, status["action"]["command"])
        self.assertIn("--no-wait --json", status["action"]["command"])

    def test_base_inspect_stops_at_auth_guard_before_resource_reads(self):
        runner = FakeRunner(
            lambda args: {
                "identity": "user",
                "verified": True,
                "identities": {
                    "user": {
                        "status": "ready",
                        "available": True,
                        "verified": True,
                        "tokenStatus": "valid",
                        "scope": "",
                    }
                },
            }
        )
        with self.assertRaises(SourceCaptureError) as raised:
            inspect_base(
                runner=runner,
                base_token="bas1",
                table_id="tbl1",
                view_id="vew1",
            )
        self.assertEqual("SOURCE_AUTH_REQUIRED", raised.exception.code)
        self.assertEqual(
            sorted(BASE_READ_SCOPES),
            raised.exception.details["missing_scopes"],
        )
        self.assertEqual(1, len(runner.calls))

    def test_meegle_server_unreachable_is_not_reported_as_login_failure(self):
        error = _process_failure(
            stdout=json.dumps(
                {
                    "authenticated": False,
                    "reason": "server unreachable: timeout",
                }
            ),
            stderr="",
            returncode=2,
            provider="Meego",
        )
        self.assertEqual("SOURCE_NETWORK_ERROR", error.code)

    def test_lark_missing_scope_preserves_authorization_hint(self):
        error = _process_failure(
            stdout="",
            stderr=json.dumps(
                {
                    "ok": False,
                    "error": {
                        "type": "authorization",
                        "subtype": "missing_scope",
                        "message": "missing required scope(s): base:record:read",
                        "hint": "run lark-cli auth login --scope base:record:read",
                        "missing_scopes": ["base:record:read"],
                    },
                }
            ),
            returncode=3,
            provider="飞书多维表格",
        )
        self.assertEqual("SOURCE_AUTH_REQUIRED", error.code)
        self.assertIn("base:record:read", error.hint)
        self.assertEqual(
            ["base:record:read"],
            error.details["missing_scopes"],
        )

    def test_resource_permission_is_not_misclassified_as_login(self):
        error = _process_failure(
            stdout="",
            stderr=json.dumps(
                {
                    "ok": False,
                    "error": {
                        "type": "authorization",
                        "code": 91403,
                        "message": "forbidden",
                        "hint": "request resource access",
                    },
                }
            ),
            returncode=3,
            provider="飞书多维表格",
        )
        self.assertEqual("SOURCE_PERMISSION_DENIED", error.code)

    def test_lark_notice_does_not_hide_success_data(self):
        response = _unwrap_response(
            {
                "data": {"records": [{"record_id": "rec1"}]},
                "_notice": {"update": {"message": "available"}},
            }
        )
        self.assertEqual("rec1", response.data["records"][0]["record_id"])

    def test_generic_success_envelope_is_unwrapped(self):
        response = _unwrap_response(
            {
                "status": "success",
                "data": {"columns": ["value"], "rows": [[1]]},
                "error": None,
                "context": {"execution_time_ms": 1},
            }
        )
        self.assertEqual(["value"], response.data["columns"])

    def test_meegle_unauthenticated_status_is_classified_stably(self):
        code, _ = _classify_cli_error(
            '{"authenticated":false,"host":null,"reason":"no local token"}'
        )
        self.assertEqual("SOURCE_AUTH_REQUIRED", code)

    def test_capture_cannot_be_written_into_skill_repo(self):
        with self.assertRaisesRegex(SourceCaptureError, "不得写入"):
            write_capture(
                ROOT / "raw_data" / "snapshot.json",
                {"business": "secret"},
                skill_root=ROOT,
            )

    def test_capture_is_written_atomically_outside_skill_repo(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            output = Path(temporary) / "snapshot.json"
            write_capture(
                output,
                {"records": [{"record_id": "1"}]},
                skill_root=ROOT,
            )
            self.assertEqual(
                {"records": [{"record_id": "1"}]},
                json.loads(output.read_text(encoding="utf-8")),
            )
            self.assertEqual([], list(output.parent.glob("*.tmp")))


class CaptureDiffTests(unittest.TestCase):
    @staticmethod
    def capture(records, *, source_uid="meego:p:v"):
        snapshot = {
            "schema_version": "byteworker-source-snapshot/v1",
            "source_type": "meego",
            "source_uid": source_uid,
            "coordinates": {"project_key": "p", "view_id": "v"},
            "fields": ["name", "status"],
            "records": records,
        }
        return {
            "snapshot": snapshot,
            "source_type": "meego",
            "source_uid": source_uid,
            "content_hash": "sha256:test",
        }

    def test_first_snapshot_is_baseline(self):
        result = diff_captures(
            current=self.capture(
                [{"work_item_id": "1", "name": "A"}]
            )
        )
        self.assertEqual(1, result["summary"]["baseline"])
        self.assertEqual("baseline", result["changes"][0]["change_type"])

    def test_diff_classifies_added_changed_and_left_view(self):
        previous = self.capture(
            [
                {"work_item_id": "1", "name": "A", "status": "doing"},
                {"work_item_id": "2", "name": "B", "status": "doing"},
            ]
        )
        current = self.capture(
            [
                {"work_item_id": "1", "name": "A", "status": "done"},
                {"work_item_id": "3", "name": "C", "status": "doing"},
            ]
        )
        result = diff_captures(current=current, previous=previous)
        self.assertEqual(1, result["summary"]["added"])
        self.assertEqual(1, result["summary"]["changed"])
        self.assertEqual(1, result["summary"]["left_view"])
        changed = next(
            item for item in result["changes"]
            if item["change_type"] == "changed"
        )
        self.assertEqual(["status"], changed["changed_paths"])
        self.assertIn("不等于", result["left_view_semantics"])

    def test_diff_rejects_mismatched_sources(self):
        with self.assertRaisesRegex(SourceCaptureError, "同一个"):
            diff_captures(
                current=self.capture([]),
                previous=self.capture([], source_uid="meego:p:other"),
            )

    def test_aeolus_diff_uses_stable_report_record(self):
        previous = {
            "source_type": "aeolus",
            "source_uid": "aeolus:cn:1:2:3",
            "content_hash": "sha256:before",
            "snapshot": {
                "source_type": "aeolus",
                "source_uid": "aeolus:cn:1:2:3",
                "records": [
                    {
                        "record_id": "report:9",
                        "report_id": 9,
                        "rows": [{"value": "1"}],
                    }
                ],
            },
        }
        current = json.loads(json.dumps(previous))
        current["content_hash"] = "sha256:after"
        current["snapshot"]["records"][0]["rows"][0]["value"] = "2"
        result = diff_captures(current=current, previous=previous)
        self.assertEqual(1, result["summary"]["changed"])
        self.assertEqual("report:9", result["changes"][0]["record_id"])


if __name__ == "__main__":
    unittest.main()
