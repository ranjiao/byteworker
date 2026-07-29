import json
import os
import stat
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from aeolus_client import (  # noqa: E402
    AeolusClient,
    AeolusClientError,
    HttpResponse,
    parse_dashboard_url,
)


def response(payload, status=200):
    return HttpResponse(
        status=status,
        headers={"content-type": "application/json"},
        body=json.dumps(payload).encode(),
    )


class RecordingTransport:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        call = {
            "method": method,
            "url": url,
            "headers": dict(headers),
            "body": json.loads(body) if body else None,
            "timeout": timeout,
        }
        self.calls.append(call)
        return self.handler(call)


class AeolusClientTests(unittest.TestCase):
    def test_dashboard_url_requires_stable_coordinates(self):
        parsed = parse_dashboard_url(
            "https://data.bytedance.net/aeolus/pages/dashboard/202"
            "?appId=101&sheetId=303"
        )
        self.assertEqual("cn", parsed["region"])
        self.assertEqual(101, parsed["app_id"])
        self.assertEqual(202, parsed["dashboard_id"])
        self.assertEqual(303, parsed["sheet_id"])

        with self.assertRaises(AeolusClientError):
            parse_dashboard_url(
                "https://data.bytedance.net/aeolus/pages/dashboard/202"
            )

    def test_bytecloud_jwt_is_exchanged_in_memory(self):
        def handle(call):
            if call["url"].endswith("/titan/passport/id"):
                self.assertEqual("jwt-secret", call["headers"]["x-jwt-token"])
                return response(
                    {
                        "code": 0,
                        "data": {"titan_passport_id": "passport-secret"},
                    }
                )
            self.assertIn(
                "titan_passport_id=passport-secret",
                call["headers"]["Cookie"],
            )
            return response({"code": "aeolus/ok", "data": {}})

        transport = RecordingTransport(handle)
        client = AeolusClient(
            credentials={"bytecloud_jwt": "jwt-secret"},
            transport=transport,
        )
        status = client.auth_status()
        self.assertTrue(status["ready"])
        self.assertEqual("bytecloud_jwt", status["auth_type"])
        self.assertEqual(2, len(transport.calls))

    def test_auth_file_must_be_owner_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            auth_file = Path(tmp) / "aeolus.json"
            auth_file.write_text(
                json.dumps({"titan_passport": "secret"}),
                encoding="utf-8",
            )
            auth_file.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)
            with patch.dict(
                os.environ,
                {"BYTEWORKER_AEOLUS_AUTH_FILE": str(auth_file)},
                clear=False,
            ):
                with self.assertRaises(AeolusClientError) as raised:
                    AeolusClient.from_environment()
            self.assertEqual(
                "AEOLUS_AUTH_FILE_PERMISSIONS",
                raised.exception.code,
            )

    def test_auth_file_cannot_live_inside_forbidden_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            auth_file = root / "aeolus.json"
            auth_file.write_text(
                json.dumps({"titan_passport": "secret"}),
                encoding="utf-8",
            )
            auth_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
            with patch.dict(
                os.environ,
                {"BYTEWORKER_AEOLUS_AUTH_FILE": str(auth_file)},
                clear=False,
            ):
                with self.assertRaises(AeolusClientError) as raised:
                    AeolusClient.from_environment(
                        forbidden_roots=(root,),
                    )
            self.assertEqual(
                "AEOLUS_AUTH_FILE_LOCATION",
                raised.exception.code,
            )

    def test_native_dashboard_and_query_flow(self):
        def handle(call):
            url = call["url"]
            if "dashboardAndSheet" in url:
                return response(
                    {
                        "code": "aeolus/ok",
                        "data": {
                            "dashboard": {"name": "Example dashboard"},
                            "sheet": {
                                "id": 303,
                                "reportIdList": [401],
                            },
                            "reportList": [
                                {
                                    "id": 401,
                                    "dataSetId": 501,
                                    "name": "Example report",
                                    "displayType": "table",
                                    "statusCode": "resolved",
                                }
                            ],
                        },
                    }
                )
            if "/sheet/simpleSheet" in url:
                return response(
                    {
                        "code": "aeolus/ok",
                        "data": {"content": {"componentTree": {}}},
                    }
                )
            if "/dataset/501/dimMet" in url:
                return response(
                    {
                        "code": "aeolus/ok",
                        "data": {
                            "dataSetName": "Example dataset",
                            "dimMetList": [
                                {
                                    "id": 601,
                                    "name": "department",
                                    "mapType": 0,
                                    "dataTypeName": "string",
                                }
                            ],
                        },
                    }
                )
            if "/dataMart/report" in url:
                return response(
                    {
                        "code": "aeolus/ok",
                        "data": {
                            "appId": 101,
                            "dataSetId": 501,
                            "reqJson": {
                                "query": {
                                    "whereList": [],
                                    "dimMetList": [
                                        {"name": "department"}
                                    ],
                                },
                                "schema": {"whereList": []},
                                "originalSchema": {"whereList": []},
                            },
                        },
                    }
                )
            if "/vizQuery/query" in url:
                self.assertEqual(10, call["body"]["query"]["limit"])
                self.assertEqual(
                    ["Example"],
                    call["body"]["query"]["whereList"][0]["val"],
                )
                self.assertEqual(
                    601,
                    call["body"]["schema"]["whereList"][0]["dimMetId"],
                )
                return response(
                    {
                        "code": "aeolus/ok",
                        "requestId": "request-1",
                        "data": {
                            "columns": [
                                {
                                    "unique_id": 601,
                                    "name": "department",
                                }
                            ],
                            "vizData": {
                                "datasets": [{"601": "Example"}]
                            },
                        },
                    }
                )
            raise AssertionError(f"unexpected URL: {url}")

        transport = RecordingTransport(handle)
        client = AeolusClient(
            credentials={"titan_passport": "passport-secret"},
            transport=transport,
        )
        url = (
            "https://data.bytedance.net/aeolus/pages/dashboard/202"
            "?appId=101&sheetId=303"
        )
        coordinates, resolved = client.resolve_dashboard(url)
        self.assertEqual("Example dashboard", resolved["dashboardName"])
        self.assertEqual([501], resolved["reports"][0]["datasetIds"])
        self.assertIn("content", client.get_sheet(coordinates=coordinates, url=url))
        fields = client.get_dataset_fields(501)
        self.assertEqual("department", fields["dimensions"][0]["name"])
        result = client.query_report(
            coordinates=coordinates,
            report_id=401,
            dataset_id=501,
            where_filters=[
                {
                    "name": "department",
                    "dimMetId": 601,
                    "op": "in",
                    "val": ["Example"],
                }
            ],
            limit=10,
            source_url=url,
        )
        self.assertEqual(["department"], result["columns"])
        self.assertEqual([["Example"]], result["rows"])

    def test_nested_card_uses_column_ids_not_object_order(self):
        result = AeolusClient._parse_viz_query(
            {
                "code": "aeolus/ok",
                "data": {
                    "columns": [
                        {"unique_id": 11, "name": "expected"},
                        {"unique_id": 22, "name": "actual"},
                    ],
                    "vizData": {
                        "datasets": [
                            [[[{"22": 120, "11": 100}]]]
                        ]
                    },
                },
            },
            request_id="request-1",
            req_json={},
        )
        self.assertEqual(
            {"expected": 100, "actual": 120},
            result["rows"][0][0][0][0],
        )


if __name__ == "__main__":
    unittest.main()
