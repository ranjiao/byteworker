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
    base_auth_status,
    capture_base,
    capture_meego,
    diff_captures,
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


if __name__ == "__main__":
    unittest.main()
