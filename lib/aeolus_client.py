"""Minimal read-only Aeolus HTTP client used by byteworker.

This module intentionally has no third-party CLI dependency.  It owns only the
small API surface byteworker needs for deterministic dashboard snapshots:
dashboard/sheet discovery, dataset fields, saved report configuration, and
VizQuery execution.

Credentials are read from environment variables or an owner-only JSON file.
They are never persisted, logged, or included in returned data.
"""

from __future__ import annotations

import copy
import json
import os
import re
import stat
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, MutableMapping, Sequence


DEFAULT_AUTH_FILE = Path("~/.config/byteworker/aeolus-auth.json").expanduser()
CN_HOSTS = {"data.bytedance.net"}
CN_TITAN_PASSPORT_ENDPOINT = "https://do.bytedance.net/titan/passport/id"
API_PATH = "/aeolus/api/v3"
VIZ_QUERY_PATH = "/aeolus/vqs/api/v2/vizQuery/query"
USER_AGENT = "byteworker-aeolus/1.0"


class AeolusClientError(RuntimeError):
    """Stable transport/auth/API failure without credential-bearing details."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        hint: str = "",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint
        self.details = dict(details or {})


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


Transport = Callable[
    [str, str, Mapping[str, str], bytes | None, int],
    HttpResponse,
]


def _urllib_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout_seconds: int,
) -> HttpResponse:
    request = urllib.request.Request(
        url,
        data=body,
        headers=dict(headers),
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return HttpResponse(
                status=response.status,
                headers=dict(response.headers.items()),
                body=response.read(),
            )
    except urllib.error.HTTPError as exc:
        return HttpResponse(
            status=exc.code,
            headers=dict(exc.headers.items()) if exc.headers else {},
            body=exc.read(),
        )
    except (urllib.error.URLError, TimeoutError) as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, TimeoutError):
            raise AeolusClientError(
                "AEOLUS_TIMEOUT",
                "风神请求超时",
                hint="缩小看板读取范围或稍后重试。",
            ) from exc
        raise AeolusClientError(
            "AEOLUS_NETWORK_ERROR",
            "无法连接风神服务",
            hint="确认当前网络可以访问公司内网风神站点。",
        ) from exc


def _nonempty(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _positive_int(value: Any, field: str) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise AeolusClientError(
            "AEOLUS_INVALID_RESPONSE",
            f"风神响应中缺少合法的 {field}",
        ) from exc
    if parsed <= 0:
        raise AeolusClientError(
            "AEOLUS_INVALID_RESPONSE",
            f"风神响应中缺少合法的 {field}",
        )
    return parsed


def parse_dashboard_url(url: str) -> dict[str, Any]:
    """Parse the stable coordinates required by the dashboard read path."""
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError as exc:
        raise AeolusClientError(
            "AEOLUS_INVALID_URL",
            "风神 dashboard URL 无法解析",
        ) from exc
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in CN_HOSTS:
        raise AeolusClientError(
            "AEOLUS_UNSUPPORTED_URL",
            "当前只支持中国区风神 dashboard URL",
            hint="请使用 data.bytedance.net 的 dashboard 页面链接。",
        )
    match = re.fullmatch(
        r"/aeolus/pages/dashboard/([1-9][0-9]*)/?",
        parsed.path,
    )
    query = urllib.parse.parse_qs(parsed.query)
    if not match:
        raise AeolusClientError(
            "AEOLUS_UNSUPPORTED_URL",
            "风神来源必须是 dashboard 页面 URL",
        )
    try:
        app_id = _positive_int(query.get("appId", [""])[0], "appId")
        sheet_id = _positive_int(query.get("sheetId", [""])[0], "sheetId")
    except AeolusClientError as exc:
        raise AeolusClientError(
            "AEOLUS_INVALID_URL",
            "风神 dashboard URL 必须包含 appId 和 sheetId",
            hint="从浏览器复制当前 sheet 的完整地址。",
        ) from exc
    return {
        "region": "cn",
        "host": host,
        "base_url": f"https://{host}",
        "app_id": app_id,
        "dashboard_id": int(match.group(1)),
        "sheet_id": sheet_id,
    }


def _collect_positive_ints(values: Sequence[Any]) -> list[int]:
    result: set[int] = set()
    for value in values:
        try:
            parsed = int(str(value))
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            result.add(parsed)
    return sorted(result)


def _walk_mappings(value: Any):
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk_mappings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_mappings(child)


def _sheet_report_ids(sheet: Mapping[str, Any]) -> list[int]:
    direct_values: list[Any] = []
    for key in ("reportIdList", "externalReportIdList"):
        value = sheet.get(key)
        if isinstance(value, list):
            direct_values.extend(value)
    direct = _collect_positive_ints(direct_values)
    if direct:
        return direct
    content = sheet.get("content")
    return _collect_positive_ints(
        [
            props.get("reportId")
            for item in _walk_mappings(content)
            if isinstance((props := item.get("props")), Mapping)
        ]
    )


def _where_ids(item: Any) -> set[str]:
    if not isinstance(item, Mapping):
        return set()
    return {
        str(item[key]).strip()
        for key in ("dimMetId", "id", "originId")
        if item.get(key) not in (None, "")
    }


def _where_matches(left: Any, right: Mapping[str, Any]) -> bool:
    if _where_ids(left) & _where_ids(right):
        return True
    if not isinstance(left, Mapping):
        return False
    return _nonempty(left.get("name")) == _nonempty(right.get("name")) != ""


def _merge_where(
    saved: Any,
    overrides: Sequence[Mapping[str, Any]],
) -> list[Any]:
    merged = list(saved) if isinstance(saved, list) else []
    for override in overrides:
        index = next(
            (
                idx
                for idx, candidate in enumerate(merged)
                if _where_matches(candidate, override)
            ),
            -1,
        )
        if index < 0:
            merged.append(dict(override))
        elif isinstance(merged[index], Mapping):
            merged[index] = {**merged[index], **override}
        else:
            merged[index] = dict(override)
    return merged


class AeolusClient:
    """Read-only Aeolus client with an injectable HTTP boundary."""

    def __init__(
        self,
        *,
        credentials: Mapping[str, Any] | None = None,
        timeout_seconds: int = 180,
        transport: Transport | None = None,
    ) -> None:
        self.credentials = {
            key: value
            for key, value in dict(credentials or {}).items()
            if _nonempty(value)
        }
        self.timeout_seconds = timeout_seconds
        self.transport = transport or _urllib_transport
        self._auth_headers: dict[str, str] | None = None
        self._auth_mode = self._resolve_auth_mode()

    @classmethod
    def from_environment(
        cls,
        *,
        timeout_seconds: int = 180,
        transport: Transport | None = None,
        forbidden_roots: Sequence[Path] = (),
    ) -> "AeolusClient":
        values: dict[str, Any] = {}
        explicit_path = _nonempty(os.environ.get("BYTEWORKER_AEOLUS_AUTH_FILE"))
        auth_path = Path(explicit_path).expanduser() if explicit_path else DEFAULT_AUTH_FILE
        resolved_auth_path = auth_path.resolve(strict=False)
        for root in forbidden_roots:
            try:
                resolved_auth_path.relative_to(root.resolve())
            except ValueError:
                continue
            raise AeolusClientError(
                "AEOLUS_AUTH_FILE_LOCATION",
                "风神凭据文件不得放在 byteworker skill 仓库内",
                hint="移动到 ~/.config/byteworker/ 或其它仓库外私密目录。",
            )
        if explicit_path and not auth_path.exists():
            raise AeolusClientError(
                "AEOLUS_AUTH_FILE_INVALID",
                f"指定的风神凭据文件不存在: {auth_path}",
            )
        if auth_path.exists():
            mode = stat.S_IMODE(auth_path.stat().st_mode)
            if mode & 0o077:
                raise AeolusClientError(
                    "AEOLUS_AUTH_FILE_PERMISSIONS",
                    f"风神凭据文件权限过宽: {auth_path}",
                    hint=f"运行 chmod 600 {auth_path} 后重试。",
                )
            try:
                payload = json.loads(auth_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise AeolusClientError(
                    "AEOLUS_AUTH_FILE_INVALID",
                    f"无法读取风神凭据文件: {auth_path}",
                ) from exc
            if not isinstance(payload, Mapping):
                raise AeolusClientError(
                    "AEOLUS_AUTH_FILE_INVALID",
                    "风神凭据文件顶层必须是 JSON 对象",
                )
            values.update(payload)
        env_map = {
            "titan_passport": "BYTEWORKER_AEOLUS_TITAN_PASSPORT",
            "bytecloud_jwt": "BYTEWORKER_AEOLUS_BYTECLOUD_JWT",
            "bearer_token": "BYTEWORKER_AEOLUS_BEARER_TOKEN",
            "client_id": "BYTEWORKER_AEOLUS_CLIENT_ID",
            "client_secret": "BYTEWORKER_AEOLUS_CLIENT_SECRET",
        }
        for key, env_name in env_map.items():
            env_value = _nonempty(os.environ.get(env_name))
            if env_value:
                values[key] = env_value
        return cls(
            credentials=values,
            timeout_seconds=timeout_seconds,
            transport=transport,
        )

    def _resolve_auth_mode(self) -> str | None:
        if self.credentials.get("titan_passport"):
            return "titan_passport"
        if self.credentials.get("bytecloud_jwt"):
            return "bytecloud_jwt"
        if self.credentials.get("bearer_token"):
            return "bearer_token"
        if self.credentials.get("client_id") and self.credentials.get("client_secret"):
            return "client_credentials"
        return None

    def auth_status(self) -> dict[str, Any]:
        if self._auth_mode is None:
            return {
                "configured": False,
                "authenticated": False,
                "authorized": False,
                "ready": False,
                "auth_type": None,
            }
        try:
            self._api_request(
                "GET",
                "https://data.bytedance.net"
                f"{API_PATH}/home/myAuthorized?offset=0&limit=1",
            )
        except AeolusClientError as exc:
            if exc.code in {
                "AEOLUS_AUTH_REQUIRED",
                "AEOLUS_PERMISSION_DENIED",
            }:
                return {
                    "configured": True,
                    "authenticated": False,
                    "authorized": False,
                    "ready": False,
                    "auth_type": self._auth_mode,
                }
            raise
        return {
            "configured": True,
            "authenticated": True,
            "authorized": True,
            "ready": True,
            "auth_type": self._auth_mode,
        }

    def _common_headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }

    def _get_auth_headers(self, *, force_refresh: bool = False) -> dict[str, str]:
        if force_refresh:
            self._auth_headers = None
        if self._auth_headers is not None:
            return dict(self._auth_headers)
        mode = self._auth_mode
        if mode is None:
            raise AeolusClientError(
                "AEOLUS_AUTH_REQUIRED",
                "尚未配置 byteworker 的风神只读凭据",
                hint=(
                    "设置 BYTEWORKER_AEOLUS_TITAN_PASSPORT、"
                    "BYTEWORKER_AEOLUS_BYTECLOUD_JWT，或配置独立 client credentials。"
                ),
            )
        if mode == "titan_passport":
            value = _nonempty(self.credentials.get("titan_passport"))
            cookie = value if "=" in value else f"titan_passport_id={value}"
            headers = {"Cookie": cookie}
        elif mode == "bytecloud_jwt":
            response = self._request_json(
                "POST",
                CN_TITAN_PASSPORT_ENDPOINT,
                headers={
                    **self._common_headers(),
                    "x-jwt-token": _nonempty(
                        self.credentials.get("bytecloud_jwt")
                    ),
                },
                payload={},
                authenticated=False,
            )
            if not isinstance(response, Mapping):
                raise AeolusClientError(
                    "AEOLUS_AUTH_REQUIRED",
                    "ByteCloud JWT 交换返回了无效响应",
                )
            data = response.get("data")
            passport = (
                data.get("titan_passport_id")
                if isinstance(data, Mapping)
                else None
            )
            if response.get("code") not in (0, "0") or not _nonempty(passport):
                raise AeolusClientError(
                    "AEOLUS_AUTH_REQUIRED",
                    "ByteCloud JWT 无法交换为风神用户会话",
                    hint="更新 BYTEWORKER_AEOLUS_BYTECLOUD_JWT 后重试。",
                )
            headers = {"Cookie": f"titan_passport_id={passport}"}
        elif mode == "bearer_token":
            headers = {
                "Authorization": "Bearer "
                + _nonempty(self.credentials.get("bearer_token"))
            }
        else:
            response = self._request_json(
                "POST",
                "https://data.bytedance.net"
                f"{API_PATH}/openapi/jwtToken",
                headers={**self._common_headers(), "Cookie": "locale=en-us"},
                payload={
                    "metadata": {
                        "clientId": self.credentials["client_id"],
                        "clientSecret": self.credentials["client_secret"],
                        "expire": 3600,
                    }
                },
                authenticated=False,
            )
            if not isinstance(response, Mapping):
                raise AeolusClientError(
                    "AEOLUS_AUTH_REQUIRED",
                    "风神 client credentials 交换返回了无效响应",
                )
            data = response.get("data")
            token = data.get("jwtToken") if isinstance(data, Mapping) else None
            if response.get("code") != "aeolus/ok" or not _nonempty(token):
                raise AeolusClientError(
                    "AEOLUS_AUTH_REQUIRED",
                    "风神 client credentials 无法换取访问令牌",
                    hint="检查 client_id/client_secret 及资源授权。",
                )
            headers = {"Authorization": f"Bearer {token}"}
        self._auth_headers = headers
        return dict(headers)

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        payload: Any = None,
        authenticated: bool = True,
        retry_auth: bool = True,
    ) -> Any:
        request_headers = self._common_headers()
        if authenticated:
            request_headers.update(self._get_auth_headers())
        request_headers.update(headers or {})
        encoded = (
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            if payload is not None
            else None
        )
        response = self.transport(
            method,
            url,
            request_headers,
            encoded,
            self.timeout_seconds,
        )
        try:
            parsed = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            if response.status in (401, 403):
                parsed = {}
            else:
                raise AeolusClientError(
                    "AEOLUS_INVALID_RESPONSE",
                    "风神返回了非 JSON 响应",
                ) from exc
        if response.status in (401, 403):
            if (
                authenticated
                and retry_auth
                and self._auth_mode in {"bytecloud_jwt", "client_credentials"}
            ):
                self._get_auth_headers(force_refresh=True)
                return self._request_json(
                    method,
                    url,
                    headers=headers,
                    payload=payload,
                    authenticated=authenticated,
                    retry_auth=False,
                )
            raise AeolusClientError(
                "AEOLUS_AUTH_REQUIRED",
                "风神身份已失效或未被服务接受",
            )
        if response.status < 200 or response.status >= 300:
            raise AeolusClientError(
                "AEOLUS_HTTP_ERROR",
                f"风神请求失败（HTTP {response.status}）",
            )
        return parsed

    def _api_request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        payload: Any = None,
    ) -> Any:
        response = self._request_json(
            method,
            url,
            headers=headers,
            payload=payload,
        )
        if not isinstance(response, Mapping):
            raise AeolusClientError(
                "AEOLUS_INVALID_RESPONSE",
                "风神 API 返回的不是对象",
            )
        code = response.get("code")
        if code not in ("aeolus/ok", 0, "0"):
            message = _nonempty(response.get("msg")) or _nonempty(
                response.get("message")
            )
            normalized = str(code or "AEOLUS_ERROR")
            lower = f"{normalized} {message}".lower()
            if (
                "unauthorized" in lower
                or "permission" in lower
                or "无权限" in lower
            ):
                error_code = "AEOLUS_PERMISSION_DENIED"
            elif "forbidden" in lower or "未登陆" in lower:
                error_code = "AEOLUS_AUTH_REQUIRED"
            elif "not found" in lower or "不存在" in lower:
                error_code = "AEOLUS_NOT_FOUND"
            else:
                error_code = "AEOLUS_API_ERROR"
            raise AeolusClientError(
                error_code,
                f"风神 API 错误: {message or normalized}",
            )
        return response.get("data")

    def resolve_dashboard(self, url: str) -> tuple[dict[str, Any], dict[str, Any]]:
        coordinates = parse_dashboard_url(url)
        base_url = coordinates["base_url"]
        request_id = (
            "byteworker.aeolus.dashboard."
            f"{coordinates['dashboard_id']}.{coordinates['sheet_id']}.{uuid.uuid4()}"
        )
        data = self._api_request(
            "POST",
            f"{base_url}/aeolus/glue/api/v1/dashboard/dashboardAndSheet"
            f"?x-request-id={urllib.parse.quote(request_id)}",
            headers={
                "request-id": request_id,
                "request-source": "prefetch",
                "x-aeolus-gray-env": "undefined",
                "x-request-id": request_id,
                "App-Id": str(coordinates["app_id"]),
                "Origin": base_url,
                "Referer": url,
            },
            payload={
                "dashboardId": coordinates["dashboard_id"],
                "sheetId": coordinates["sheet_id"],
                "isEdit": 0,
                "preQuery": True,
                "enableDepartmentFilter": True,
                "viewport": "1290,805",
            },
        )
        if not isinstance(data, Mapping):
            raise AeolusClientError(
                "AEOLUS_INVALID_RESPONSE",
                "风神 dashboard 响应缺少 data",
            )
        dashboard = data.get("dashboard")
        sheet = data.get("sheet")
        report_list = data.get("reportList")
        if (
            not isinstance(dashboard, Mapping)
            or not isinstance(sheet, Mapping)
            or not isinstance(report_list, list)
        ):
            raise AeolusClientError(
                "AEOLUS_INVALID_RESPONSE",
                "风神 dashboard 响应缺少 dashboard、sheet 或 reportList",
            )
        report_ids = set(_sheet_report_ids(sheet))
        reports = []
        for item in report_list:
            if not isinstance(item, Mapping):
                continue
            try:
                report_id = _positive_int(item.get("id"), "reportId")
            except AeolusClientError:
                continue
            if report_ids and report_id not in report_ids:
                continue
            dataset_values = [item.get("dataSetId")]
            if isinstance(item.get("dataSetIdList"), list):
                dataset_values.extend(item["dataSetIdList"])
            dataset_ids = _collect_positive_ints(dataset_values)
            reports.append(
                {
                    "reportId": report_id,
                    "datasetIds": dataset_ids,
                    "name": _nonempty(item.get("name")),
                    "displayType": _nonempty(item.get("displayType")),
                    "statusCode": _nonempty(item.get("statusCode")),
                    "updatedAt": item.get("mtime"),
                }
            )
        reports.sort(key=lambda item: item["reportId"])
        resolved = {
            "region": "cn",
            "urlType": "dashboard",
            "appId": coordinates["app_id"],
            "dashboardId": coordinates["dashboard_id"],
            "sheetId": coordinates["sheet_id"],
            "dashboardName": _nonempty(dashboard.get("name")),
            "reports": reports,
        }
        public_coordinates = {
            key: coordinates[key]
            for key in (
                "region",
                "app_id",
                "dashboard_id",
                "sheet_id",
            )
        }
        return public_coordinates, resolved

    def get_sheet(self, *, coordinates: Mapping[str, Any], url: str) -> dict[str, Any]:
        base_url = "https://data.bytedance.net"
        query = urllib.parse.urlencode(
            {
                "sheetId": coordinates["sheet_id"],
                "dashboardId": coordinates["dashboard_id"],
                "isEdit": 0,
                "rawLocaleConfig": "true",
            }
        )
        data = self._api_request(
            "GET",
            f"{base_url}{API_PATH}/sheet/simpleSheet?{query}",
            headers={
                "App-Id": str(coordinates["app_id"]),
                "Data-Format-Unit": "auto",
                "Origin": base_url,
                "Referer": url,
            },
        )
        if not isinstance(data, Mapping):
            raise AeolusClientError(
                "AEOLUS_INVALID_RESPONSE",
                "风神 simpleSheet 响应缺少对象 data",
            )
        return dict(data)

    def get_dataset_fields(self, dataset_id: int) -> dict[str, Any]:
        data = self._api_request(
            "GET",
            "https://data.bytedance.net"
            f"/aeolus/api/v4/open/dataset/{dataset_id}/dimMet",
        )
        if not isinstance(data, Mapping):
            raise AeolusClientError(
                "AEOLUS_INVALID_RESPONSE",
                f"风神 dataset {dataset_id} 字段响应无效",
            )
        dimensions = []
        metrics = []
        for item in data.get("dimMetList") or []:
            if not isinstance(item, Mapping):
                continue
            target = metrics if item.get("mapType") == 1 else dimensions
            target.append(
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "dataTypeName": item.get("dataTypeName"),
                }
            )
        return {
            "datasetId": dataset_id,
            "datasetName": _nonempty(data.get("dataSetName")),
            "dimensions": dimensions,
            "metrics": metrics,
        }

    def _get_report_detail(self, report_id: int) -> dict[str, Any]:
        query = urllib.parse.urlencode({"reportId": report_id})
        data = self._api_request(
            "GET",
            "https://data.bytedance.net"
            f"{API_PATH}/dataMart/report?{query}",
        )
        if not isinstance(data, Mapping):
            raise AeolusClientError(
                "AEOLUS_INVALID_RESPONSE",
                f"风神 report {report_id} 配置响应无效",
            )
        return dict(data)

    @staticmethod
    def _normalize_where(
        value: Mapping[str, Any],
        *,
        index: int,
        dataset_id: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        field_id = str(value["dimMetId"])
        name = str(value["name"])
        unique_id = f"byteworker-{index}-{uuid.uuid4()}"
        option = value.get("option") or {
            "isReportFilter": False,
            "isWhereInAggr": True,
            "isDefaultPartitionField": name == "partition_date",
        }
        query_item = {
            "name": name,
            "id": field_id,
            "preRelation": value.get("preRelation", "and"),
            "uniqueId": unique_id,
            "op": value["op"],
            "val": value["val"],
            "valOption": value.get("valOption", {}),
            "option": option,
        }
        schema_item = {
            "aggrConf": {},
            "id": field_id,
            "originId": field_id,
            "dimMetId": int(field_id),
            "dataSetId": dataset_id,
            "uniqueId": unique_id,
            "highlight": False,
            "format": {},
            "showEditComponent": False,
            "location": "whereList",
            "preRelation": query_item["preRelation"],
            "name": name,
            "dataTypeName": "date" if name == "partition_date" else "string",
            "index": index,
            "roleType": 0,
            "filter": {
                "op": value["op"],
                "val": value["val"],
                "valOption": query_item["valOption"],
                "option": option,
            },
            "unremovable": name == "partition_date",
            "undraggable": False,
        }
        return query_item, schema_item

    @classmethod
    def _build_report_body(
        cls,
        req_json: Mapping[str, Any],
        *,
        request_id: str,
        dataset_id: int,
        where_filters: Sequence[Mapping[str, Any]],
        limit: int,
    ) -> dict[str, Any]:
        body = copy.deepcopy(dict(req_json))
        body["requestId"] = request_id
        query = body.get("query")
        query = dict(query) if isinstance(query, Mapping) else {}
        query["limit"] = limit
        normalized = [
            cls._normalize_where(
                value,
                index=index,
                dataset_id=dataset_id,
            )
            for index, value in enumerate(where_filters)
        ]
        if normalized:
            query["whereList"] = _merge_where(
                query.get("whereList"),
                [item[0] for item in normalized],
            )
            for key in ("schema", "originalSchema"):
                schema = body.get(key)
                schema = dict(schema) if isinstance(schema, Mapping) else {}
                schema["whereList"] = [
                    (
                        {**value, "index": index}
                        if isinstance(value, Mapping)
                        else value
                    )
                    for index, value in enumerate(
                        _merge_where(
                            schema.get("whereList"),
                            [item[1] for item in normalized],
                        )
                    )
                ]
                body[key] = schema
        body["query"] = query
        return body

    @staticmethod
    def _parse_viz_query(
        response: Any,
        *,
        request_id: str,
        req_json: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(response, Mapping):
            raise AeolusClientError(
                "AEOLUS_INVALID_RESPONSE",
                "风神 VizQuery 返回的不是对象",
            )
        code = response.get("code")
        if code not in (None, "", 0, 200, "0", "aeolus/ok"):
            message = _nonempty(response.get("message")) or _nonempty(
                response.get("msg")
            )
            raise AeolusClientError(
                "AEOLUS_API_ERROR",
                f"风神 VizQuery 失败: {message or code}",
            )
        node = response.get("data")
        node = node if isinstance(node, Mapping) else response
        raw_columns = node.get("columns")
        raw_columns = raw_columns if isinstance(raw_columns, list) else []
        keys: list[str] = []
        columns: list[str] = []
        for item in raw_columns:
            if not isinstance(item, Mapping):
                continue
            key = item.get("unique_id")
            if key in (None, ""):
                key = item.get("dm_id", item.get("alias"))
            if key in (None, ""):
                continue
            keys.append(str(key))
            columns.append(
                _nonempty(item.get("name"))
                or _nonempty(item.get("alias"))
                or str(key)
            )
        viz_data = node.get("vizData")
        datasets = (
            viz_data.get("datasets")
            if isinstance(viz_data, Mapping)
            else None
        )
        if isinstance(datasets, list) and columns:
            def remap_nested(value: Any) -> Any:
                if isinstance(value, Mapping):
                    if all(key in value for key in keys):
                        return {
                            column: value[key]
                            for key, column in zip(keys, columns)
                        }
                    return {
                        key: remap_nested(child)
                        for key, child in value.items()
                    }
                if isinstance(value, list):
                    return [remap_nested(child) for child in value]
                return value

            rows = [
                [row.get(key) for key in keys]
                if isinstance(row, Mapping)
                else remap_nested(row)
                for row in datasets
            ]
        else:
            rows = node.get("rows")
            if not isinstance(rows, list):
                rows = node.get("data")
            if not isinstance(rows, list):
                rows = node.get("rowList")
            rows = rows if isinstance(rows, list) else []
            if not columns:
                query = req_json.get("query")
                dim_met = (
                    query.get("dimMetList")
                    if isinstance(query, Mapping)
                    else None
                )
                columns = [
                    _nonempty(item.get("name"))
                    for item in (dim_met or [])
                    if isinstance(item, Mapping) and _nonempty(item.get("name"))
                ]
        return {
            "requestId": response.get("requestId") or request_id,
            "columns": columns,
            "rows": rows,
        }

    def query_report(
        self,
        *,
        coordinates: Mapping[str, Any],
        report_id: int,
        dataset_id: int,
        where_filters: Sequence[Mapping[str, Any]],
        limit: int,
        source_url: str,
    ) -> dict[str, Any]:
        detail = self._get_report_detail(report_id)
        resolved_dataset = _positive_int(detail.get("dataSetId"), "dataSetId")
        if resolved_dataset != dataset_id:
            raise AeolusClientError(
                "AEOLUS_INVALID_RESPONSE",
                f"风神 report {report_id} 的 dataset 已发生变化",
            )
        req_json = detail.get("reqJson")
        if not isinstance(req_json, Mapping):
            raise AeolusClientError(
                "AEOLUS_INVALID_RESPONSE",
                f"风神 report {report_id} 缺少可重放的保存态查询",
            )
        app_id = _positive_int(
            detail.get("appId", coordinates.get("app_id")),
            "appId",
        )
        request_id = (
            "byteworker.aeolus.vizQuery."
            f"app_{app_id}.report_{report_id}.dataset_{dataset_id}.{uuid.uuid4()}"
        )
        body = self._build_report_body(
            req_json,
            request_id=request_id,
            dataset_id=dataset_id,
            where_filters=where_filters,
            limit=limit,
        )
        base_url = "https://data.bytedance.net"
        response = self._request_json(
            "POST",
            f"{base_url}{VIZ_QUERY_PATH}?"
            + urllib.parse.urlencode({"requestId": request_id}),
            headers={
                "Referer": source_url,
                "Origin": base_url,
                "x-app-id": str(app_id),
            },
            payload=body,
        )
        return self._parse_viz_query(
            response,
            request_id=request_id,
            req_json=req_json,
        )
