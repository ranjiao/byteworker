"""Read-only source inspection and canonical snapshot capture.

The adapters deliberately shell out to the official CLIs.  Authentication,
API evolution, and transport details remain owned by those CLIs; byteworker
owns source identity, completeness checks, canonicalization, and provenance
locators.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import unquote_plus


INSPECT_SCHEMA = "byteworker-source-inspect/v1"
CAPTURE_SCHEMA = "byteworker-source-capture/v1"
SNAPSHOT_SCHEMA = "byteworker-source-snapshot/v1"
DIFF_SCHEMA = "byteworker-source-diff/v1"
AUTH_SCHEMA = "byteworker-source-auth/v1"
DEFAULT_MAX_ITEMS = 1000
MAX_PAGES = 200
LOCATOR_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
BASE_READ_SCOPES = (
    "base:app:read",
    "base:table:read",
    "base:field:read",
    "base:view:read",
    "base:record:read",
)
SUPPORTED_MEEGO_VIEW_KINDS = {
    "view_story",
    "view_issue",
    "view_workitem",
}
SENSITIVE_QUERY_KEYS = {
    "access_token",
    "auth_token",
    "authorization",
    "credential",
    "disposable_login_token",
    "secret",
    "sign",
    "signature",
    "token",
}
URL_RE = re.compile(r"https?://[^\s<>\"']+")
MEEGO_WORK_ITEM_TYPES = {
    "view_story": "story",
    "view_issue": "issue",
}


class SourceCaptureError(RuntimeError):
    """A stable, user-actionable source adapter failure."""

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

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code, "message": str(self)}
        if self.hint:
            result["hint"] = self.hint
        if self.details:
            result["details"] = self.details
        return result


@dataclass(frozen=True)
class CliResponse:
    data: Any
    meta: Mapping[str, Any]
    raw: Any


class CommandRunner:
    """Small injectable subprocess boundary used by both adapters."""

    def __init__(self, binary: str, *, timeout_seconds: int = 180) -> None:
        self.binary = binary
        self.timeout_seconds = timeout_seconds

    def run(self, args: Sequence[str], *, provider: str) -> CliResponse:
        completed = self._invoke(args, provider=provider)
        if completed.returncode != 0:
            raise _process_failure(
                stdout=completed.stdout,
                stderr=completed.stderr,
                returncode=completed.returncode,
                provider=provider,
            )
        try:
            raw = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise SourceCaptureError(
                "SOURCE_INVALID_RESPONSE",
                f"{provider} CLI 未返回合法 JSON",
                details={"stdout": _bounded(completed.stdout)},
            ) from exc
        return _unwrap_response(raw)

    def run_status(
        self,
        args: Sequence[str],
        *,
        provider: str,
    ) -> CliResponse:
        """Run an auth status command, preserving a structured logged-out result."""
        completed = self._invoke(args, provider=provider)
        structured_text = completed.stdout.strip() or completed.stderr.strip()
        try:
            raw = json.loads(structured_text)
        except json.JSONDecodeError as exc:
            if completed.returncode != 0:
                raise _process_failure(
                    stdout=completed.stdout,
                    stderr=completed.stderr,
                    returncode=completed.returncode,
                    provider=provider,
                ) from exc
            raise SourceCaptureError(
                "SOURCE_INVALID_RESPONSE",
                f"{provider} CLI 未返回合法 JSON",
                details={"stdout": _bounded(completed.stdout)},
            ) from exc
        if completed.returncode != 0 and not (
            isinstance(raw, Mapping) and raw.get("authenticated") is False
        ):
            raise _process_failure(
                stdout=completed.stdout,
                stderr=completed.stderr,
                returncode=completed.returncode,
                provider=provider,
            )
        return CliResponse(data=raw, meta={}, raw=raw)

    def _invoke(
        self,
        args: Sequence[str],
        *,
        provider: str,
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [self.binary, *args],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout_seconds,
                check=False,
                env={
                    **os.environ,
                    "LARKSUITE_CLI_NO_UPDATE_NOTIFIER": "1",
                    "LARKSUITE_CLI_NO_SKILLS_NOTIFIER": "1",
                },
            )
        except FileNotFoundError as exc:
            raise SourceCaptureError(
                "SOURCE_CLI_MISSING",
                f"找不到 {self.binary}",
                hint=f"先安装并确认 {self.binary} 在 PATH 中。",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise SourceCaptureError(
                "SOURCE_TIMEOUT",
                f"{provider} 读取超时",
                hint="缩小视图范围后重试；超时结果不会生成快照。",
            ) from exc


def _bounded(value: str, limit: int = 2048) -> str:
    normalized = " ".join(value.split())
    return normalized if len(normalized) <= limit else normalized[: limit - 1] + "…"


def _classify_cli_error(message: str) -> tuple[str, str]:
    lower = message.lower()
    if "91403" in lower or "permission denied" in lower or "forbidden" in lower:
        return (
            "SOURCE_PERMISSION_DENIED",
            "当前用户缺少目标资源的读取权限；请让 Base / 表 / 视图或 Meego 空间的所有者"
            "授予访问权限。重复登录或自动切换 bot 通常不能解决。",
        )
    if any(
        token in lower
        for token in (
            "not logged in",
            "not authenticated",
            '"authenticated": false',
            '"authenticated":false',
            "no local token",
            "no credential",
            "missing_scopes",
            "missing_scope",
            "missing required scope",
            "auth login",
            "login required",
            "unauthorized",
        )
    ):
        return (
            "SOURCE_AUTH_REQUIRED",
            "先完成对应 CLI 的用户身份登录，再重试只读检查。",
        )
    if "not found" in lower or "不存在" in lower:
        return ("SOURCE_NOT_FOUND", "重新检查 URL 和视图是否仍然存在。")
    return ("SOURCE_CLI_ERROR", "查看底层 CLI 错误并修正后重试。")


def _process_failure(
    *,
    stdout: str,
    stderr: str,
    returncode: int,
    provider: str,
) -> SourceCaptureError:
    raw: Any = None
    structured_text = stdout.strip() or stderr.strip()
    if structured_text:
        try:
            raw = json.loads(structured_text)
        except json.JSONDecodeError:
            raw = None
    if isinstance(raw, Mapping):
        authenticated = raw.get("authenticated")
        if authenticated is False:
            reason = str(raw.get("reason", "")).strip()
            if reason.lower().startswith("server unreachable"):
                return SourceCaptureError(
                    "SOURCE_NETWORK_ERROR",
                    f"{provider} 认证服务暂时不可达",
                    hint="稍后重试；网络失败不会生成快照。",
                    details={"exit_code": returncode},
                )
            host_value = raw.get("host")
            host = str(host_value).strip() if host_value not in (None, "") else ""
            hint = "运行 meegle auth login 完成 OAuth 登录。"
            if host:
                hint = f"运行 meegle auth login --host {host} 完成 OAuth 登录。"
            return SourceCaptureError(
                "SOURCE_AUTH_REQUIRED",
                f"{provider} CLI 尚未登录",
                hint=hint,
                details={"exit_code": returncode},
            )
        error = raw.get("error")
        if isinstance(error, Mapping):
            message = str(
                error.get("message")
                or error.get("error")
                or f"{provider} CLI 命令执行失败"
            )
            classifier_input = json.dumps(raw, ensure_ascii=False)
            code, default_hint = _classify_cli_error(classifier_input)
            details: dict[str, Any] = {"exit_code": returncode}
            for key in ("type", "subtype", "missing_scopes"):
                if error.get(key) not in (None, "", []):
                    details[key] = error[key]
            return SourceCaptureError(
                code,
                _bounded(message),
                hint=_bounded(str(error.get("hint") or default_hint)),
                details=details,
            )
    message = _bounded(stderr or stdout or f"{provider} CLI 命令执行失败")
    code, hint = _classify_cli_error(message)
    return SourceCaptureError(
        code,
        message,
        hint=hint,
        details={"exit_code": returncode},
    )


def _unwrap_response(raw: Any) -> CliResponse:
    if not isinstance(raw, Mapping):
        return CliResponse(data=raw, meta={}, raw=raw)
    error = raw.get("error")
    if error not in (None, "", {}, []):
        if isinstance(error, Mapping):
            message = str(error.get("message") or error.get("error") or error)
        else:
            message = str(error)
        code, hint = _classify_cli_error(message)
        raise SourceCaptureError(code, _bounded(message), hint=hint)

    meta = raw.get("meta") if isinstance(raw.get("meta"), Mapping) else {}
    if "data" in raw and (
        "meta" in raw
        or "error" in raw
        or set(raw).issubset(
            {"code", "msg", "message", "data", "meta", "error", "_notice"}
        )
    ):
        return CliResponse(data=raw.get("data"), meta=meta, raw=raw)
    return CliResponse(data=raw, meta=meta, raw=raw)


def _walk_mappings(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk_mappings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_mappings(child)


def _find_values(value: Any, keys: Sequence[str]) -> list[Any]:
    result: list[Any] = []
    for item in _walk_mappings(value):
        for key in keys:
            if key in item and item[key] not in (None, ""):
                result.append(item[key])
    return result


def _first_value(value: Any, keys: Sequence[str], default: Any = "") -> Any:
    values = _find_values(value, keys)
    return values[0] if values else default


def _shallow_value(value: Any, keys: Sequence[str], default: Any = "") -> Any:
    if not isinstance(value, Mapping):
        return default
    for key in keys:
        candidate = value.get(key)
        if candidate not in (None, ""):
            return candidate
    for wrapper in ("view", "base", "table", "info", "metadata"):
        nested = value.get(wrapper)
        if not isinstance(nested, Mapping):
            continue
        for key in keys:
            candidate = nested.get(key)
            if candidate not in (None, ""):
                return candidate
    return default


def _extract_items(value: Any, keys: Sequence[str]) -> list[Any]:
    if isinstance(value, list):
        return value
    if not isinstance(value, Mapping):
        return []
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, list):
            return candidate
    for key in ("data", "result", "page"):
        candidate = value.get(key)
        if isinstance(candidate, Mapping):
            found = _extract_items(candidate, keys)
            if found:
                return found
    return []


def _extract_record_items(
    value: Any,
    *,
    keys: Sequence[str],
    id_keys: Sequence[str],
) -> list[Any]:
    candidates: list[list[Any]] = []

    def visit(current: Any) -> None:
        if isinstance(current, Mapping):
            for key in keys:
                candidate = current.get(key)
                if isinstance(candidate, list):
                    candidates.append(candidate)
            for child in current.values():
                visit(child)
        elif isinstance(current, list):
            for child in current:
                visit(child)

    if isinstance(value, list):
        candidates.append(value)
    visit(value)
    if not candidates:
        return []
    candidates.sort(
        key=lambda items: (
            sum(1 for item in items if _stable_id(item, id_keys)),
            len(items),
        ),
        reverse=True,
    )
    return candidates[0]


def _pagination(value: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if not isinstance(value, Mapping):
        return result
    sources = [value]
    for key in ("pagination", "page_info", "pageInfo"):
        if isinstance(value.get(key), Mapping):
            sources.append(value[key])
    for source in sources:
        for source_key, target_key in (
            ("has_more", "has_more"),
            ("hasMore", "has_more"),
            ("total", "total"),
            ("total_count", "total"),
            ("totalCount", "total"),
            ("next_offset", "next_offset"),
            ("nextOffset", "next_offset"),
            ("next_page_token", "next_page_token"),
            ("nextPageToken", "next_page_token"),
            ("truncated", "truncated"),
        ):
            if source_key in source and target_key not in result:
                result[target_key] = source[source_key]
    return result


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in {"true", "false"}:
        return value.lower() == "true"
    return None


def _stable_id(item: Any, keys: Sequence[str]) -> str:
    if not isinstance(item, Mapping):
        return ""
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return str(value)
    for wrapper in (
        "work_item",
        "workItem",
        "work_item_info",
        "workItemInfo",
        "work_item_attribute",
        "workItemAttribute",
        "record",
    ):
        nested = item.get(wrapper)
        if not isinstance(nested, Mapping):
            continue
        for key in keys:
            value = nested.get(key)
            if value not in (None, ""):
                return str(value)
    return ""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sanitize_url(url: str) -> tuple[str, int]:
    fragment_marker = ""
    base = url
    if "#" in base:
        base, fragment = base.split("#", 1)
        fragment_marker = "#" + fragment
    if "?" not in base:
        return url, 0
    prefix, query = base.split("?", 1)
    kept: list[str] = []
    removed = 0
    for part in query.split("&"):
        key = unquote_plus(part.split("=", 1)[0]).strip().lower()
        if key in SENSITIVE_QUERY_KEYS:
            removed += 1
        else:
            kept.append(part)
    sanitized = prefix
    if kept:
        sanitized += "?" + "&".join(kept)
    return sanitized + fragment_marker, removed


def _sanitize_source_value(value: Any) -> tuple[Any, int]:
    if isinstance(value, Mapping):
        result: dict[Any, Any] = {}
        removed = 0
        for key, child in value.items():
            sanitized, count = _sanitize_source_value(child)
            result[key] = sanitized
            removed += count
        return result, removed
    if isinstance(value, list):
        result_list: list[Any] = []
        removed = 0
        for child in value:
            sanitized, count = _sanitize_source_value(child)
            result_list.append(sanitized)
            removed += count
        return result_list, removed
    if isinstance(value, str):
        removed = 0

        def replace(match: re.Match[str]) -> str:
            nonlocal removed
            sanitized, count = _sanitize_url(match.group(0))
            removed += count
            return sanitized

        return URL_RE.sub(replace, value), removed
    return value, 0


def _sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _captured_at() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _normalized_fields(fields: Sequence[str]) -> list[str]:
    return sorted(
        {
            str(field).strip()
            for field in fields
            if str(field).strip()
        }
    )


def _source_time(item: Any) -> str:
    value = _first_value(
        item,
        (
            "updated_at",
            "updated_time",
            "update_time",
            "modified_at",
            "modified_time",
        ),
        "",
    )
    return str(value) if value not in (None, "") else ""


def _label(item: Any, fallback: str) -> str:
    value = _first_value(
        item,
        (
            "name",
            "title",
            "work_item_name",
            "workItemName",
            "summary",
            "subject",
            "record_name",
        ),
        "",
    )
    return str(value).strip() or fallback


def _unique_string(value: Any, keys: Sequence[str], *, field: str) -> str:
    values = {str(item).strip() for item in _find_values(value, keys) if str(item).strip()}
    if len(values) == 1:
        return values.pop()
    if not values:
        raise SourceCaptureError(
            "SOURCE_COORDINATES_MISSING",
            f"响应中缺少 {field}",
        )
    raise SourceCaptureError(
        "SOURCE_AMBIGUOUS",
        f"响应中存在多个 {field}: {', '.join(sorted(values))}",
        hint="请提供更精确的 URL 或显式 ID。",
    )


def _run_auth_status(
    runner: CommandRunner,
    args: Sequence[str],
    *,
    provider: str,
) -> Any:
    status_method = getattr(runner, "run_status", None)
    if callable(status_method):
        return status_method(args, provider=provider).data
    return runner.run(args, provider=provider).data


def meego_auth_status(
    *,
    runner: CommandRunner,
    host: str = "",
) -> dict[str, Any]:
    response = _run_auth_status(
        runner,
        ["auth", "status", "--format", "json"],
        provider="Meego",
    )
    authenticated = _first_value(response, ("authenticated",), False)
    resolved_host = str(
        _first_value(response, ("host",), host)
    ).strip() or host.strip()
    login_command = (
        f"meegle auth login --host {resolved_host}"
        if resolved_host
        else "meegle auth login --host <project.feishu.cn|meegle.com|自定义域名>"
    )
    return {
        "schema_version": AUTH_SCHEMA,
        "source_type": "meego",
        "authenticated": authenticated is True,
        "authorized": authenticated is True,
        "ready": authenticated is True,
        "host": resolved_host or None,
        "required_scopes": [],
        "missing_scopes": [],
        "action": None
        if authenticated is True
        else {
            "kind": "login",
            "command": login_command,
            "interactive": True,
            "message": "Meego 使用独立 OAuth；先选择与资源 URL 一致的站点再登录。",
        },
    }


def _meego_auth(
    runner: CommandRunner,
    *,
    host: str = "",
) -> Mapping[str, Any]:
    status = meego_auth_status(runner=runner, host=host)
    if status["ready"] is not True:
        action = status["action"]
        raise SourceCaptureError(
            "SOURCE_AUTH_REQUIRED",
            "Meego CLI 尚未登录",
            hint=f"运行 {action['command']} 完成 OAuth 登录。",
            details={
                "source_type": "meego",
                "auth_action": action,
            },
        )
    return status


def _scope_set(value: Any) -> set[str]:
    if isinstance(value, str):
        return {
            item
            for item in re.split(r"[\s,]+", value.strip())
            if item
        }
    if isinstance(value, (list, tuple, set)):
        return {
            str(item).strip()
            for item in value
            if str(item).strip()
        }
    return set()


def base_auth_status(*, runner: CommandRunner) -> dict[str, Any]:
    response = _run_auth_status(
        runner,
        # auth status defaults to JSON across the supported CLI line; older
        # releases reject the otherwise redundant --json flag.
        ["auth", "status", "--verify"],
        provider="飞书",
    )
    if not isinstance(response, Mapping):
        raise SourceCaptureError(
            "SOURCE_INVALID_RESPONSE",
            "lark-cli auth status 返回的不是对象",
        )
    identities = response.get("identities")
    user = (
        identities.get("user")
        if isinstance(identities, Mapping)
        and isinstance(identities.get("user"), Mapping)
        else {}
    )
    authenticated = bool(
        user.get("available") is True
        and str(user.get("status", "")).lower() == "ready"
        and str(user.get("tokenStatus", "")).lower() == "valid"
    )
    verified = bool(
        user.get("verified") is True
        or (
            str(response.get("identity", "")).lower() == "user"
            and response.get("verified") is True
        )
    )
    granted_scopes = _scope_set(user.get("scope"))
    missing_scopes = sorted(set(BASE_READ_SCOPES) - granted_scopes)
    ready = authenticated and verified and not missing_scopes
    scope_argument = " ".join(BASE_READ_SCOPES)
    if not authenticated:
        action_kind = "login_and_authorize"
        action_message = (
            "先完成 lark-cli 用户登录并授予 Base 摄取所需的最小只读 scopes。"
        )
    else:
        action_kind = "authorize"
        action_message = "当前用户已登录，但还缺少 Base 摄取所需的只读 scopes。"
    return {
        "schema_version": AUTH_SCHEMA,
        "source_type": "feishu_base",
        "identity": "user",
        "authenticated": authenticated,
        "verified": verified,
        "authorized": not missing_scopes,
        "ready": ready,
        "required_scopes": list(BASE_READ_SCOPES),
        "missing_scopes": missing_scopes,
        "action": None
        if ready
        else {
            "kind": action_kind,
            "command": (
                f'lark-cli auth login --scope "{scope_argument}" '
                "--no-wait --json"
            ),
            "interactive": True,
            "requires_qr": True,
            "message": action_message,
        },
    }


def _base_auth(runner: CommandRunner) -> Mapping[str, Any]:
    status = base_auth_status(runner=runner)
    if status["ready"] is not True:
        action = status["action"]
        missing = ", ".join(status["missing_scopes"])
        message = (
            "lark-cli 用户身份尚未登录"
            if not status["authenticated"]
            else f"lark-cli 用户身份缺少 Base 只读 scopes: {missing}"
        )
        raise SourceCaptureError(
            "SOURCE_AUTH_REQUIRED",
            message,
            hint=(
                f"运行 {action['command']} 发起授权；把返回的 verification_url 原样"
                "展示给用户并生成二维码，用户确认完成后再用 device_code 收尾。"
            ),
            details={
                "source_type": "feishu_base",
                "missing_scopes": status["missing_scopes"],
                "auth_action": action,
            },
        )
    return status


def _resolve_meego(
    *,
    runner: CommandRunner,
    url: str,
    project_key: str,
    view_id: str,
) -> tuple[dict[str, str], Mapping[str, Any]]:
    decoded: Mapping[str, Any] = {}
    simple_name = ""
    if url:
        value = runner.run(
            ["url", "decode", "--url", url, "--format", "json"],
            provider="Meego",
        ).data
        if not isinstance(value, Mapping):
            raise SourceCaptureError(
                "SOURCE_INVALID_RESPONSE",
                "Meego URL 解析结果不是对象",
            )
        decoded = value
        url_kind = str(value.get("url_kind", ""))
        if url_kind not in SUPPORTED_MEEGO_VIEW_KINDS:
            raise SourceCaptureError(
                "SOURCE_UNSUPPORTED_URL",
                "第一版只支持项目内需求 / 缺陷 / 通用工作项保存视图，"
                f"当前 url_kind={url_kind or 'unknown'}",
            )
        view_id = view_id or str(value.get("view_id", "")).strip()
        simple_name = str(value.get("simple_name", "")).strip()
    if not view_id:
        raise SourceCaptureError("SOURCE_COORDINATES_MISSING", "缺少 Meego view_id")

    _meego_auth(
        runner,
        host=str(decoded.get("host", "")).strip() if decoded else "",
    )
    if not project_key:
        if not simple_name:
            raise SourceCaptureError(
                "SOURCE_COORDINATES_MISSING",
                "缺少 Meego project_key；URL 也没有可解析的 simple_name",
            )
        result = runner.run(
            [
                "--envelope",
                "project",
                "search",
                "--params",
                json.dumps({"project_key": simple_name}, ensure_ascii=False),
                "--format",
                "json",
            ],
            provider="Meego",
        ).data
        project_key = _unique_string(
            result,
            ("project_key", "projectKey"),
            field="project_key",
        )
    return {
        "project_key": project_key,
        "view_id": view_id,
    }, decoded


def _meego_work_item_type(decoded: Mapping[str, Any]) -> str:
    explicit = str(
        decoded.get("work_item_type")
        or decoded.get("workItemType")
        or ""
    ).strip()
    if explicit:
        return explicit
    url_kind = str(decoded.get("url_kind", "")).strip()
    return MEEGO_WORK_ITEM_TYPES.get(url_kind, "")


def _meego_view_args(
    *,
    project_key: str,
    view_id: str,
    fields: Sequence[str],
    auto_paginate: bool,
) -> list[str]:
    result: list[str] = []
    if auto_paginate:
        result.append("--auto-paginate")
    result.extend(
        [
            "--envelope",
            "view",
            "get",
            "--project-key",
            project_key,
            "--view-id",
            view_id,
            "--page-num",
            "1",
        ]
    )
    for field in fields:
        result.extend(["--fields", field])
    result.extend(["--format", "json"])
    return result


def _canonical_meego_field_schema(field: Any) -> dict[str, Any]:
    if not isinstance(field, Mapping):
        return {}
    aliases = (
        ("field_key", ("field_key", "fieldKey", "key", "id")),
        ("field_name", ("field_name", "fieldName", "name")),
        ("field_type", ("field_type", "fieldType", "type")),
        ("field_desc", ("field_desc", "fieldDesc", "description")),
    )
    result: dict[str, Any] = {}
    for target, keys in aliases:
        for key in keys:
            if key in field and field[key] not in (None, ""):
                result[target] = field[key]
                break
    return result


def _meego_fields(
    *,
    runner: CommandRunner,
    project_key: str,
    work_item_type: str,
) -> list[dict[str, Any]]:
    if not work_item_type:
        return []
    response = runner.run(
        [
            "--auto-paginate",
            "--envelope",
            "workitem",
            "meta-fields",
            "--project-key",
            project_key,
            "--work-item-type",
            work_item_type,
            "--page-num",
            "1",
            "--format",
            "json",
        ],
        provider="Meego",
    )
    fields = _extract_items(response.data, ("list", "fields", "items"))
    canonical = [
        value
        for value in (_canonical_meego_field_schema(item) for item in fields)
        if value.get("field_key")
    ]
    canonical.sort(key=lambda item: str(item["field_key"]))
    return canonical


def inspect_meego(
    *,
    runner: CommandRunner,
    url: str = "",
    project_key: str = "",
    view_id: str = "",
    fields: Sequence[str] = (),
) -> dict[str, Any]:
    fields = _normalized_fields(fields)
    coordinates, decoded = _resolve_meego(
        runner=runner,
        url=url,
        project_key=project_key,
        view_id=view_id,
    )
    work_item_type = _meego_work_item_type(decoded)
    field_schema = _meego_fields(
        runner=runner,
        project_key=coordinates["project_key"],
        work_item_type=work_item_type,
    )
    response = runner.run(
        _meego_view_args(
            project_key=coordinates["project_key"],
            view_id=coordinates["view_id"],
            fields=fields or ["name"],
            auto_paginate=False,
        ),
        provider="Meego",
    )
    items = _extract_record_items(
        response.data,
        keys=(
            "work_item_list",
            "workItemList",
            "work_items",
            "workItems",
            "items",
            "records",
            "list",
        ),
        id_keys=("work_item_id", "workItemId", "id"),
    )
    page = _pagination(response.data)
    title = str(
        _shallow_value(
            response.data,
            ("view_name", "viewName", "title", "name"),
            "",
        )
    ).strip()
    source_uid = (
        f"meego:{coordinates['project_key']}:{coordinates['view_id']}"
    )
    return {
        "schema_version": INSPECT_SCHEMA,
        "source_type": "meego",
        "source_uid": source_uid,
        "source_url": url,
        "title": title or f"Meego view {coordinates['view_id']}",
        "coordinates": coordinates,
        "selector": {"kind": "saved_view"},
        "requested_fields": fields,
        "work_item_type": work_item_type or None,
        "fields": field_schema,
        "field_count": len(field_schema),
        "sample_item_count": len(items),
        "estimated_total": page.get("total"),
        "has_more": _as_bool(page.get("has_more")),
        "url_kind": decoded.get("url_kind") if decoded else None,
        "read_only": True,
    }


def capture_meego(
    *,
    runner: CommandRunner,
    url: str = "",
    project_key: str = "",
    view_id: str = "",
    fields: Sequence[str],
    max_items: int = DEFAULT_MAX_ITEMS,
) -> dict[str, Any]:
    fields = _normalized_fields(fields)
    if not fields:
        raise SourceCaptureError(
            "SOURCE_FIELDS_REQUIRED",
            "Meego capture 至少需要一个 --field",
            hint="先运行 source inspect，再选择稳定字段 key。",
        )
    coordinates, _ = _resolve_meego(
        runner=runner,
        url=url,
        project_key=project_key,
        view_id=view_id,
    )
    response = runner.run(
        _meego_view_args(
            project_key=coordinates["project_key"],
            view_id=coordinates["view_id"],
            fields=fields,
            auto_paginate=True,
        ),
        provider="Meego",
    )
    page = _pagination(response.data)
    meta_page = _pagination(response.meta)
    truncated = (
        _as_bool(page.get("truncated")) is True
        or _as_bool(meta_page.get("truncated")) is True
    )
    has_more = _as_bool(page.get("has_more"))
    if truncated or has_more is True:
        raise SourceCaptureError(
            "SOURCE_INCOMPLETE",
            "Meego 自动分页仍返回未完成结果",
            hint="缩小保存视图范围后重试；不完整结果不会生成快照。",
        )
    records = _extract_record_items(
        response.data,
        keys=(
            "work_item_list",
            "workItemList",
            "work_items",
            "workItems",
            "items",
            "records",
            "list",
        ),
        id_keys=("work_item_id", "workItemId", "id"),
    )
    if len(records) > max_items:
        raise SourceCaptureError(
            "SOURCE_LIMIT_EXCEEDED",
            f"Meego 视图包含 {len(records)} 条，超过上限 {max_items}",
            hint="缩小保存视图或显式提高 --max-items。",
        )
    sanitized_records, removed_sensitive_parameters = _sanitize_source_value(records)
    ordered = _sort_and_validate_records(
        sanitized_records,
        id_keys=("work_item_id", "workItemId", "id"),
        provider="Meego",
    )
    source_uid = (
        f"meego:{coordinates['project_key']}:{coordinates['view_id']}"
    )
    snapshot = {
        "schema_version": SNAPSHOT_SCHEMA,
        "source_type": "meego",
        "source_uid": source_uid,
        "coordinates": coordinates,
        "fields": fields,
        "records": ordered,
    }
    anchors = [
        {
            "anchor_id": f"workitem:{_stable_id(item, ('work_item_id', 'workItemId', 'id'))}",
            "kind": "meego_workitem",
            "precision": "exact",
            "locator": {
                "project_key": coordinates["project_key"],
                "work_item_id": _stable_id(
                    item, ("work_item_id", "workItemId", "id")
                ),
                "view_id": coordinates["view_id"],
            },
            "open_url": url,
            "label": _label(
                item,
                _stable_id(item, ("work_item_id", "workItemId", "id")),
            ),
            **({"source_time": _source_time(item)} if _source_time(item) else {}),
        }
        for item in ordered
    ]
    return {
        "schema_version": CAPTURE_SCHEMA,
        "capture_mode": "snapshot",
        "captured_at": _captured_at(),
        "source_type": "meego",
        "source_uid": source_uid,
        "source_url": url,
        "title": str(
            _shallow_value(
                response.data,
                ("view_name", "viewName", "title", "name"),
                f"Meego view {coordinates['view_id']}",
            )
        ),
        "coordinates": coordinates,
        "requested_fields": fields,
        "pagination": {
            "complete": True,
            "item_count": len(ordered),
            "auto_paginated": True,
        },
        "sanitization": {
            "removed_sensitive_query_parameters": removed_sensitive_parameters,
        },
        "snapshot": snapshot,
        "content_hash": _sha256(snapshot),
        "anchors": anchors,
    }


def _base_args(command: str, **values: Any) -> list[str]:
    result = ["base", command]
    for key, value in values.items():
        if value in (None, "", []):
            continue
        flag = "--" + key.replace("_", "-")
        if isinstance(value, (list, tuple)):
            for item in value:
                result.extend([flag, str(item)])
        else:
            result.extend([flag, str(value)])
    result.extend(["--as", "user", "--format", "json"])
    return result


def _base_page(
    *,
    runner: CommandRunner,
    command: str,
    item_keys: Sequence[str],
    values: Mapping[str, Any],
    limit: int,
    max_items: int,
) -> tuple[list[Any], dict[str, Any]]:
    offset = 0
    pages = 0
    records: list[Any] = []
    seen_ids: set[str] = set()
    while pages < MAX_PAGES:
        response = runner.run(
            _base_args(command, **values, offset=offset, limit=limit),
            provider="飞书多维表格",
        )
        pages += 1
        batch = _extract_items(response.data, item_keys)
        page = _pagination(response.data)
        total = page.get("total")
        try:
            total_int = int(total) if total not in (None, "") else None
        except (TypeError, ValueError):
            total_int = None
        if total_int is not None and total_int > max_items:
            raise SourceCaptureError(
                "SOURCE_LIMIT_EXCEEDED",
                f"多维表格结果预计 {total_int} 条，超过上限 {max_items}",
                hint="缩小保存视图或显式提高 --max-items。",
            )
        if len(records) + len(batch) > max_items:
            raise SourceCaptureError(
                "SOURCE_LIMIT_EXCEEDED",
                f"多维表格结果超过上限 {max_items}",
                hint="缩小保存视图或显式提高 --max-items。",
            )
        for item in batch:
            item_id = _stable_id(item, ("record_id", "recordId", "field_id", "fieldId", "id"))
            if item_id and item_id in seen_ids:
                raise SourceCaptureError(
                    "SOURCE_PAGINATION_STALLED",
                    f"多维表格分页重复返回 ID {item_id}",
                    hint="分页未可靠推进，本次不生成快照。",
                )
            if item_id:
                seen_ids.add(item_id)
            records.append(item)

        has_more = _as_bool(page.get("has_more"))
        if has_more is False:
            break
        if has_more is None and total_int is not None:
            if len(records) >= total_int:
                break
        elif has_more is None and len(batch) < limit:
            break
        if not batch:
            raise SourceCaptureError(
                "SOURCE_PAGINATION_STALLED",
                "多维表格返回空页但仍指示存在下一页",
                hint="本次不生成不完整快照。",
            )
        next_offset = page.get("next_offset")
        try:
            candidate = int(next_offset)
        except (TypeError, ValueError):
            candidate = offset + len(batch)
        if candidate <= offset:
            raise SourceCaptureError(
                "SOURCE_PAGINATION_STALLED",
                "多维表格分页 offset 未推进",
                hint="本次不生成不完整快照。",
            )
        offset = candidate
    else:
        raise SourceCaptureError(
            "SOURCE_INCOMPLETE",
            f"多维表格分页超过安全上限 {MAX_PAGES} 页",
            hint="缩小保存视图后重试。",
        )
    return records, {
        "complete": True,
        "pages": pages,
        "item_count": len(records),
    }


def _resolve_base(
    *,
    runner: CommandRunner,
    url: str,
    base_token: str,
    table_id: str,
    view_id: str,
) -> dict[str, str]:
    if url:
        response = runner.run(
            _base_args("+url-resolve", url=url),
            provider="飞书多维表格",
        ).data
        base_token = base_token or str(
            _first_value(response, ("base_token", "baseToken", "app_token"), "")
        ).strip()
        table_id = table_id or str(
            _first_value(response, ("table_id", "tableId"), "")
        ).strip()
        view_id = view_id or str(
            _first_value(response, ("view_id", "viewId"), "")
        ).strip()
    missing = [
        name
        for name, value in (
            ("base_token", base_token),
            ("table_id", table_id),
            ("view_id", view_id),
        )
        if not value
    ]
    if missing:
        raise SourceCaptureError(
            "SOURCE_COORDINATES_MISSING",
            "第一版只支持明确 Base 视图，缺少 " + ", ".join(missing),
            hint="提供含 table/view 参数的 Base URL，或显式传入这些 ID。",
        )
    return {
        "base_token": base_token,
        "table_id": table_id,
        "view_id": view_id,
    }


def _base_metadata(
    *,
    runner: CommandRunner,
    coordinates: Mapping[str, str],
) -> tuple[dict[str, Any], list[Any]]:
    common = {
        "base_token": coordinates["base_token"],
    }
    base = runner.run(
        _base_args("+base-get", **common),
        provider="飞书多维表格",
    ).data
    table = runner.run(
        _base_args(
            "+table-get",
            **common,
            table_id=coordinates["table_id"],
        ),
        provider="飞书多维表格",
    ).data
    view = runner.run(
        _base_args(
            "+view-get",
            **common,
            table_id=coordinates["table_id"],
            view_id=coordinates["view_id"],
        ),
        provider="飞书多维表格",
    ).data
    fields, _ = _base_page(
        runner=runner,
        command="+field-list",
        item_keys=("fields", "items", "list"),
        values={
            **common,
            "table_id": coordinates["table_id"],
        },
        limit=200,
        max_items=2000,
    )
    title_parts = []
    for value in (base, table, view):
        title = str(_shallow_value(value, ("name", "title"), "")).strip()
        if title and title not in title_parts:
            title_parts.append(title)
    return {
        "title": " / ".join(title_parts),
        "base": base,
        "table": table,
        "view": view,
    }, fields


def inspect_base(
    *,
    runner: CommandRunner,
    url: str = "",
    base_token: str = "",
    table_id: str = "",
    view_id: str = "",
) -> dict[str, Any]:
    _base_auth(runner)
    coordinates = _resolve_base(
        runner=runner,
        url=url,
        base_token=base_token,
        table_id=table_id,
        view_id=view_id,
    )
    metadata, fields = _base_metadata(runner=runner, coordinates=coordinates)
    source_uid = (
        f"feishu_base:{coordinates['base_token']}:"
        f"{coordinates['table_id']}:{coordinates['view_id']}"
    )
    return {
        "schema_version": INSPECT_SCHEMA,
        "source_type": "feishu_base",
        "source_uid": source_uid,
        "source_url": url,
        "title": metadata["title"]
        or f"Base view {coordinates['view_id']}",
        "coordinates": coordinates,
        "selector": {"kind": "saved_view"},
        "fields": fields,
        "field_count": len(fields),
        "read_only": True,
    }


def _field_matches(field: Any, requested: set[str]) -> bool:
    if not isinstance(field, Mapping):
        return False
    identifiers = {
        str(field.get(key, "")).strip()
        for key in ("field_id", "fieldId", "id", "name", "field_name")
    }
    return bool((identifiers - {""}) & requested)


def _canonical_field_schema(field: Any) -> dict[str, Any]:
    if not isinstance(field, Mapping):
        return {}
    aliases = (
        ("field_id", ("field_id", "fieldId", "id")),
        ("name", ("name", "field_name", "fieldName")),
        ("type", ("type", "field_type", "fieldType", "ui_type", "uiType")),
        ("is_primary", ("is_primary", "isPrimary")),
    )
    result: dict[str, Any] = {}
    for target, keys in aliases:
        for key in keys:
            if key in field and field[key] not in (None, ""):
                result[target] = field[key]
                break
    return result


def capture_base(
    *,
    runner: CommandRunner,
    url: str = "",
    base_token: str = "",
    table_id: str = "",
    view_id: str = "",
    fields: Sequence[str],
    max_items: int = DEFAULT_MAX_ITEMS,
) -> dict[str, Any]:
    fields = _normalized_fields(fields)
    if not fields:
        raise SourceCaptureError(
            "SOURCE_FIELDS_REQUIRED",
            "多维表格 capture 至少需要一个 --field",
            hint="先运行 source inspect，从真实 field_id 或字段名中选择。",
        )
    _base_auth(runner)
    coordinates = _resolve_base(
        runner=runner,
        url=url,
        base_token=base_token,
        table_id=table_id,
        view_id=view_id,
    )
    metadata, field_schema = _base_metadata(
        runner=runner,
        coordinates=coordinates,
    )
    requested = {str(item).strip() for item in fields if str(item).strip()}
    matched_schema = [
        item for item in field_schema if _field_matches(item, requested)
    ]
    matched_names = {
        str(value).strip()
        for item in matched_schema
        if isinstance(item, Mapping)
        for value in (
            item.get("field_id"),
            item.get("fieldId"),
            item.get("id"),
            item.get("name"),
            item.get("field_name"),
        )
        if value not in (None, "")
    }
    missing = sorted(
        field
        for field in requested
        if field not in matched_names
        and not any(_field_matches(item, {field}) for item in matched_schema)
    )
    if missing:
        raise SourceCaptureError(
            "SOURCE_FIELD_NOT_FOUND",
            "多维表格不存在字段: " + ", ".join(missing),
            hint="重新运行 source inspect，使用真实 field_id 或精确字段名。",
        )
    records, pagination = _base_page(
        runner=runner,
        command="+record-list",
        item_keys=("records", "items", "list"),
        values={
            "base_token": coordinates["base_token"],
            "table_id": coordinates["table_id"],
            "view_id": coordinates["view_id"],
            "field_id": fields,
        },
        limit=200,
        max_items=max_items,
    )
    sanitized_records, removed_sensitive_parameters = _sanitize_source_value(records)
    ordered = _sort_and_validate_records(
        sanitized_records,
        id_keys=("record_id", "recordId", "id"),
        provider="多维表格",
    )
    ordered_fields = sorted(
        (_canonical_field_schema(item) for item in matched_schema),
        key=lambda item: _stable_id(
            item, ("field_id", "name")
        ),
    )
    source_uid = (
        f"feishu_base:{coordinates['base_token']}:"
        f"{coordinates['table_id']}:{coordinates['view_id']}"
    )
    snapshot = {
        "schema_version": SNAPSHOT_SCHEMA,
        "source_type": "feishu_base",
        "source_uid": source_uid,
        "coordinates": coordinates,
        "fields": ordered_fields,
        "records": ordered,
    }
    anchors = [
        {
            "anchor_id": f"record:{_stable_id(item, ('record_id', 'recordId', 'id'))}",
            "kind": "base_record",
            "precision": "exact",
            "locator": {
                "base_token": coordinates["base_token"],
                "table_id": coordinates["table_id"],
                "view_id": coordinates["view_id"],
                "record_id": _stable_id(item, ("record_id", "recordId", "id")),
            },
            "open_url": url,
            "label": _label(
                item,
                _stable_id(item, ("record_id", "recordId", "id")),
            ),
            **({"source_time": _source_time(item)} if _source_time(item) else {}),
        }
        for item in ordered
    ]
    return {
        "schema_version": CAPTURE_SCHEMA,
        "capture_mode": "snapshot",
        "captured_at": _captured_at(),
        "source_type": "feishu_base",
        "source_uid": source_uid,
        "source_url": url,
        "title": metadata["title"]
        or f"Base view {coordinates['view_id']}",
        "coordinates": coordinates,
        "requested_fields": fields,
        "pagination": pagination,
        "sanitization": {
            "removed_sensitive_query_parameters": removed_sensitive_parameters,
        },
        "snapshot": snapshot,
        "content_hash": _sha256(snapshot),
        "anchors": anchors,
    }


def _sort_and_validate_records(
    records: Sequence[Any],
    *,
    id_keys: Sequence[str],
    provider: str,
) -> list[Any]:
    keyed: list[tuple[str, Any]] = []
    seen: set[str] = set()
    for item in records:
        item_id = _stable_id(item, id_keys)
        if not item_id:
            raise SourceCaptureError(
                "SOURCE_RECORD_ID_MISSING",
                f"{provider} 返回了没有稳定 ID 的记录",
                hint="无法建立精确 provenance，本次不生成快照。",
            )
        if not LOCATOR_ID_RE.fullmatch(item_id):
            raise SourceCaptureError(
                "SOURCE_RECORD_ID_INVALID",
                f"{provider} 返回了不能作为 provenance locator 的 ID",
                hint="本次不生成可能无法验证的快照。",
            )
        if item_id in seen:
            raise SourceCaptureError(
                "SOURCE_DUPLICATE_RECORD",
                f"{provider} 返回重复记录 ID {item_id}",
                hint="确认视图分页是否稳定后重试。",
            )
        seen.add(item_id)
        keyed.append((item_id, item))
    keyed.sort(key=lambda pair: pair[0])
    return [item for _, item in keyed]


def _snapshot_from_capture(value: Mapping[str, Any], *, label: str) -> Mapping[str, Any]:
    snapshot = value.get("snapshot")
    if not isinstance(snapshot, Mapping):
        raise SourceCaptureError(
            "SOURCE_DIFF_INVALID",
            f"{label} 文件缺少 snapshot 对象",
        )
    records = snapshot.get("records")
    if not isinstance(records, list):
        raise SourceCaptureError(
            "SOURCE_DIFF_INVALID",
            f"{label} snapshot 缺少 records 数组",
        )
    return snapshot


def _diff_id_keys(source_type: str) -> tuple[str, ...]:
    if source_type == "meego":
        return ("work_item_id", "workItemId", "id")
    if source_type == "feishu_base":
        return ("record_id", "recordId", "id")
    raise SourceCaptureError(
        "SOURCE_DIFF_UNSUPPORTED",
        f"source diff 暂不支持 source_type={source_type or 'unknown'}",
    )


def _changed_paths(before: Any, after: Any, prefix: str = "") -> list[str]:
    if before == after:
        return []
    if isinstance(before, Mapping) and isinstance(after, Mapping):
        result: list[str] = []
        for key in sorted(set(before) | set(after), key=str):
            child = f"{prefix}.{key}" if prefix else str(key)
            if key not in before or key not in after:
                result.append(child)
            else:
                result.extend(_changed_paths(before[key], after[key], child))
        return result
    if isinstance(before, list) and isinstance(after, list):
        return [prefix or "$"]
    return [prefix or "$"]


def diff_captures(
    *,
    current: Mapping[str, Any],
    previous: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    current_snapshot = _snapshot_from_capture(current, label="current")
    source_type = str(current_snapshot.get("source_type", "")).strip()
    source_uid = str(current_snapshot.get("source_uid", "")).strip()
    if not source_uid:
        raise SourceCaptureError(
            "SOURCE_DIFF_INVALID",
            "current snapshot 缺少 source_uid",
        )
    previous_snapshot: Mapping[str, Any] | None = None
    if previous is not None:
        previous_snapshot = _snapshot_from_capture(previous, label="previous")
        if (
            previous_snapshot.get("source_type") != source_type
            or previous_snapshot.get("source_uid") != source_uid
        ):
            raise SourceCaptureError(
                "SOURCE_DIFF_SOURCE_MISMATCH",
                "previous / current 不是同一个结构化来源",
                hint="只比较 source_type 与 source_uid 都一致的相邻完整快照。",
            )

    id_keys = _diff_id_keys(source_type)
    current_records = _sort_and_validate_records(
        current_snapshot["records"],
        id_keys=id_keys,
        provider="current snapshot",
    )
    previous_records = (
        _sort_and_validate_records(
            previous_snapshot["records"],
            id_keys=id_keys,
            provider="previous snapshot",
        )
        if previous_snapshot is not None
        else []
    )
    current_by_id = {
        _stable_id(item, id_keys): item for item in current_records
    }
    previous_by_id = {
        _stable_id(item, id_keys): item for item in previous_records
    }

    changes: list[dict[str, Any]] = []
    unchanged = 0
    if previous_snapshot is None:
        for record_id, record in current_by_id.items():
            changes.append(
                {
                    "change_type": "baseline",
                    "record_id": record_id,
                    "after": record,
                }
            )
    else:
        for record_id in sorted(set(current_by_id) | set(previous_by_id)):
            before = previous_by_id.get(record_id)
            after = current_by_id.get(record_id)
            if before is None:
                changes.append(
                    {
                        "change_type": "added",
                        "record_id": record_id,
                        "after": after,
                    }
                )
            elif after is None:
                changes.append(
                    {
                        "change_type": "left_view",
                        "record_id": record_id,
                        "before": before,
                    }
                )
            elif before != after:
                changes.append(
                    {
                        "change_type": "changed",
                        "record_id": record_id,
                        "changed_paths": _changed_paths(before, after),
                        "before": before,
                        "after": after,
                    }
                )
            else:
                unchanged += 1

    counts = {
        key: sum(1 for item in changes if item["change_type"] == key)
        for key in ("baseline", "added", "changed", "left_view")
    }
    deterministic = {
        "schema_version": DIFF_SCHEMA,
        "source_type": source_type,
        "source_uid": source_uid,
        "previous_content_hash": (
            previous.get("content_hash") if previous is not None else None
        ),
        "current_content_hash": current.get("content_hash"),
        "summary": {
            **counts,
            "unchanged": unchanged,
            "current_total": len(current_records),
        },
        "left_view_semantics": (
            "记录只表示不再出现在当前保存视图中，不等于来源工作项已删除。"
        ),
        "changes": changes,
    }
    return {
        **deterministic,
        "generated_at": _captured_at(),
        "diff_hash": _sha256(deterministic),
    }


def read_capture(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceCaptureError(
            "SOURCE_DIFF_INVALID",
            f"无法读取 capture 文件: {path}",
        ) from exc
    if not isinstance(value, Mapping):
        raise SourceCaptureError(
            "SOURCE_DIFF_INVALID",
            f"capture 文件顶层不是对象: {path}",
        )
    return value


def write_capture(path: Path, capture: Mapping[str, Any], *, skill_root: Path) -> None:
    resolved = path.expanduser().resolve()
    root = skill_root.resolve()
    if resolved == root or root in resolved.parents:
        raise SourceCaptureError(
            "SOURCE_OUTPUT_IN_SKILL_REPO",
            "业务快照不得写入 byteworker skill 仓库",
            hint="使用系统临时目录或知识库数据目录。",
        )
    resolved.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_temporary = tempfile.mkstemp(
        dir=resolved.parent,
        prefix=f".{resolved.name}.",
        suffix=".tmp",
    )
    temporary = Path(raw_temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(capture, ensure_ascii=False, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, resolved)
    finally:
        if temporary.exists():
            temporary.unlink()
