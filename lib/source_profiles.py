"""KB-owned source profiles for deterministic structured-source refreshes.

Profiles are operational truth: one source instance, one file under the user's
KB.  They contain no credentials and no captured rows.  Raw snapshots remain
immutable evidence of what was actually read on a particular run.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qsl, urlsplit
from zoneinfo import ZoneInfo


PROFILE_SCHEMA_V1 = "byteworker-source-profile/v1"
PROFILE_SCHEMA_V2 = "byteworker-source-profile/v2"
# Kept for callers which still construct the original Aeolus profile.
PROFILE_SCHEMA = PROFILE_SCHEMA_V1
PROFILE_SCHEMAS = {PROFILE_SCHEMA_V1, PROFILE_SCHEMA_V2}
PROFILE_SOURCE_TYPES_V1 = {"aeolus"}
PROFILE_SOURCE_TYPES_V2 = {"meego", "feishu_doc"}
PROFILE_SOURCE_TYPES = PROFILE_SOURCE_TYPES_V1 | PROFILE_SOURCE_TYPES_V2
ROUTINE_CADENCES = {"daily", "weekly", "monthly"}
SHA_PREFIX = "sha256:"
SENSITIVE_KEYS = {
    "access_token",
    "api_key",
    "auth_token",
    "authorization",
    "bearer_token",
    "bytecloud_jwt",
    "client_secret",
    "credential",
    "disposable_login_token",
    "jwt",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "session_token",
    "sign",
    "signature",
    "titan_passport",
    "token",
}


class SourceProfileError(RuntimeError):
    """Safe validation or local-transaction error."""

    def __init__(self, code: str, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def profile_revision(profile: Mapping[str, Any]) -> str:
    normalized = validate_profile(profile)
    return SHA_PREFIX + hashlib.sha256(_canonical_bytes(normalized)).hexdigest()


def _positive_int(value: Any, field: str) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            f"source profile 的 {field} 必须是正整数",
        ) from exc
    if parsed <= 0:
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            f"source profile 的 {field} 必须是正整数",
        )
    return parsed


def _reject_unknown(
    value: Mapping[str, Any],
    allowed: set[str],
    field: str,
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            f"source profile 的 {field} 含未知字段: {', '.join(unknown)}",
        )


def _reject_credentials(value: Any, path: str = "profile") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in SENSITIVE_KEYS:
                raise SourceProfileError(
                    "SOURCE_PROFILE_CONTAINS_CREDENTIAL",
                    f"source profile 不得保存凭据字段: {path}.{key}",
                )
            _reject_credentials(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_credentials(child, f"{path}[{index}]")


def _validate_source_identity(
    profile: Mapping[str, Any],
) -> tuple[str, str, str]:
    source_uid = str(profile.get("source_uid", "")).strip()
    source_url = str(profile.get("source_url", "")).strip()
    title = str(profile.get("title", "")).strip()
    if not source_uid or not source_url.startswith("https://") or not title:
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            "source profile 必须包含 source_uid、HTTPS source_url 和 title",
        )
    sensitive_query_keys = sorted(
        {
            key.lower()
            for key, _ in parse_qsl(
                urlsplit(source_url).query,
                keep_blank_values=True,
            )
            if key.lower() in SENSITIVE_KEYS
        }
    )
    if sensitive_query_keys:
        raise SourceProfileError(
            "SOURCE_PROFILE_CONTAINS_CREDENTIAL",
            "source profile URL 不得保存凭据参数: "
            + ", ".join(sensitive_query_keys),
        )
    return source_uid, source_url, title


def _validate_routine(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            "source profile.routine 必须是对象",
        )
    _reject_unknown(value, {"enabled", "cadence"}, "routine")
    enabled = value.get("enabled")
    cadence = value.get("cadence")
    if not isinstance(enabled, bool):
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            "source profile.routine.enabled 必须是布尔值",
        )
    if enabled:
        cadence = str(cadence or "").strip()
        if cadence not in ROUTINE_CADENCES:
            raise SourceProfileError(
                "SOURCE_PROFILE_INVALID",
                "启用 routine 时 cadence 必须是 daily、weekly 或 monthly",
            )
    elif cadence not in (None, ""):
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            "routine.enabled=false 时 cadence 必须为空",
        )
    else:
        cadence = None
    return {
        "enabled": enabled,
        "cadence": cadence,
    }


def _validate_v1_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(profile, Mapping):
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            "source profile 顶层必须是 JSON 对象",
        )
    _reject_credentials(profile)
    _reject_unknown(
        profile,
        {
            "schema_version",
            "source_type",
            "source_uid",
            "source_url",
            "title",
            "coordinates",
            "capture",
            "routine",
        },
        "顶层",
    )
    if profile.get("schema_version") != PROFILE_SCHEMA_V1:
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            f"source profile schema_version 必须是 {PROFILE_SCHEMA_V1}",
        )
    source_type = str(profile.get("source_type", "")).strip()
    if source_type not in PROFILE_SOURCE_TYPES_V1:
        raise SourceProfileError(
            "SOURCE_PROFILE_UNSUPPORTED",
            f"source profile 暂不支持 source_type={source_type or 'missing'}",
        )
    source_uid, source_url, title = _validate_source_identity(profile)

    coordinates = profile.get("coordinates")
    if not isinstance(coordinates, Mapping):
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            "source profile.coordinates 必须是对象",
        )
    _reject_unknown(
        coordinates,
        {"region", "app_id", "dashboard_id", "sheet_id"},
        "coordinates",
    )
    region = str(coordinates.get("region", "")).strip()
    app_id = _positive_int(coordinates.get("app_id"), "coordinates.app_id")
    dashboard_id = _positive_int(
        coordinates.get("dashboard_id"),
        "coordinates.dashboard_id",
    )
    sheet_id = _positive_int(
        coordinates.get("sheet_id"),
        "coordinates.sheet_id",
    )
    expected_uid = f"aeolus:{region}:{app_id}:{dashboard_id}:{sheet_id}"
    if not region or source_uid != expected_uid:
        raise SourceProfileError(
            "SOURCE_PROFILE_IDENTITY_MISMATCH",
            "source profile 的 source_uid 与 coordinates 不一致",
        )

    capture = profile.get("capture")
    if not isinstance(capture, Mapping):
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            "source profile.capture 必须是对象",
        )
    _reject_unknown(
        capture,
        {"report_selector", "filters", "max_items_per_report"},
        "capture",
    )
    report_selector = capture.get("report_selector")
    if not isinstance(report_selector, Mapping):
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            "source profile.capture.report_selector 必须是对象",
        )
    _reject_unknown(report_selector, {"mode", "report_ids"}, "report_selector")
    report_mode = str(report_selector.get("mode", "")).strip()
    report_values = report_selector.get("report_ids", [])
    if not isinstance(report_values, list):
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            "report_selector.report_ids 必须是数组",
        )
    report_ids = sorted(
        {_positive_int(value, "report_selector.report_ids") for value in report_values}
    )
    if report_mode == "all":
        if report_ids:
            raise SourceProfileError(
                "SOURCE_PROFILE_INVALID",
                "report_selector.mode=all 时不得同时保存 report_ids",
            )
    elif report_mode == "include":
        if not report_ids:
            raise SourceProfileError(
                "SOURCE_PROFILE_INVALID",
                "report_selector.mode=include 时 report_ids 不能为空",
            )
    else:
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            "report_selector.mode 必须是 all 或 include",
        )

    filters = capture.get("filters")
    if not isinstance(filters, Mapping):
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            "source profile.capture.filters 必须是对象",
        )
    _reject_unknown(filters, {"mode", "where"}, "filters")
    filter_mode = str(filters.get("mode", "")).strip()
    if filter_mode not in {"dashboard", "explicit", "merge"}:
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            "filters.mode 必须是 dashboard、explicit 或 merge",
        )
    raw_where = filters.get("where", [])
    if not isinstance(raw_where, list):
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            "filters.where 必须是数组",
        )
    where: list[dict[str, Any]] = []
    for index, item in enumerate(raw_where):
        if not isinstance(item, Mapping):
            raise SourceProfileError(
                "SOURCE_PROFILE_INVALID",
                f"filters.where[{index}] 必须是对象",
            )
        _reject_unknown(
            item,
            {"name", "dimMetId", "op", "val"},
            f"filters.where[{index}]",
        )
        name = str(item.get("name", "")).strip()
        op = str(item.get("op", "")).strip()
        val = item.get("val")
        if not name or not op or not isinstance(val, list):
            raise SourceProfileError(
                "SOURCE_PROFILE_INVALID",
                f"filters.where[{index}] 需要 name、dimMetId、op 和数组 val",
            )
        where.append(
            {
                "name": name,
                "dimMetId": _positive_int(
                    item.get("dimMetId"),
                    f"filters.where[{index}].dimMetId",
                ),
                "op": op,
                "val": val,
            }
        )
    if filter_mode == "dashboard" and where:
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            "filters.mode=dashboard 时不得保存 where",
        )
    if filter_mode == "explicit" and not where:
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            "filters.mode=explicit 时至少需要一个 where",
        )
    max_items = _positive_int(
        capture.get("max_items_per_report"),
        "capture.max_items_per_report",
    )

    routine = _validate_routine(profile.get("routine"))

    return {
        "schema_version": PROFILE_SCHEMA_V1,
        "source_type": source_type,
        "source_uid": source_uid,
        "source_url": source_url,
        "title": title,
        "coordinates": {
            "region": region,
            "app_id": app_id,
            "dashboard_id": dashboard_id,
            "sheet_id": sheet_id,
        },
        "capture": {
            "report_selector": {
                "mode": report_mode,
                "report_ids": report_ids,
            },
            "filters": {
                "mode": filter_mode,
                "where": where,
            },
            "max_items_per_report": max_items,
        },
        "routine": routine,
    }


def _required_text(value: Any, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            f"source profile 的 {field} 不能为空",
        )
    return normalized


def _validate_meego_v2(
    *,
    source_uid: str,
    selector: Mapping[str, Any],
    capture_policy: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    _reject_unknown(selector, {"project_key", "view_id"}, "selector")
    project_key = _required_text(selector.get("project_key"), "selector.project_key")
    view_id = _required_text(selector.get("view_id"), "selector.view_id")
    expected_uid = f"meego:{project_key}:{view_id}"
    if source_uid != expected_uid:
        raise SourceProfileError(
            "SOURCE_PROFILE_IDENTITY_MISMATCH",
            "source profile 的 source_uid 与 selector 不一致",
        )

    _reject_unknown(capture_policy, {"fields", "max_items"}, "capture_policy")
    raw_fields = capture_policy.get("fields")
    if not isinstance(raw_fields, list):
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            "source profile.capture_policy.fields 必须是非空数组",
        )
    fields = sorted(
        {
            str(field).strip()
            for field in raw_fields
            if isinstance(field, str) and field.strip()
        }
    )
    if not fields or len(fields) != len(raw_fields):
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            "source profile.capture_policy.fields 必须是非空且不重复的字符串数组",
        )
    max_items = _positive_int(
        capture_policy.get("max_items"),
        "capture_policy.max_items",
    )
    return (
        {
            "project_key": project_key,
            "view_id": view_id,
        },
        {
            "fields": fields,
            "max_items": max_items,
        },
    )


def _validate_feishu_doc_v2(
    *,
    source_uid: str,
    selector: Mapping[str, Any],
    capture_policy: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    _reject_unknown(selector, {"document_id"}, "selector")
    document_id = _required_text(
        selector.get("document_id"),
        "selector.document_id",
    )
    if source_uid != document_id:
        raise SourceProfileError(
            "SOURCE_PROFILE_IDENTITY_MISMATCH",
            "source profile 的 source_uid 与 selector.document_id 不一致",
        )

    _reject_unknown(
        capture_policy,
        {"period", "comments", "whiteboards"},
        "capture_policy",
    )
    period_value = capture_policy.get("period", "")
    if period_value is None:
        period = ""
    elif isinstance(period_value, str):
        period = period_value.strip()
    else:
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            "source profile.capture_policy.period 必须是字符串或为空",
        )
    comments = capture_policy.get("comments")
    whiteboards = capture_policy.get("whiteboards")
    if not isinstance(comments, bool) or not isinstance(whiteboards, bool):
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            "source profile.capture_policy.comments/whiteboards 必须是布尔值",
        )
    return (
        {"document_id": document_id},
        {
            "period": period,
            "comments": comments,
            "whiteboards": whiteboards,
        },
    )


def _validate_v2_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    _reject_unknown(
        profile,
        {
            "schema_version",
            "source_type",
            "source_uid",
            "source_url",
            "title",
            "selector",
            "capture_policy",
            "routine",
        },
        "顶层",
    )
    source_type = str(profile.get("source_type", "")).strip()
    if source_type not in PROFILE_SOURCE_TYPES_V2:
        raise SourceProfileError(
            "SOURCE_PROFILE_UNSUPPORTED",
            f"source profile v2 暂不支持 source_type={source_type or 'missing'}",
        )
    source_uid, source_url, title = _validate_source_identity(profile)
    selector = profile.get("selector")
    capture_policy = profile.get("capture_policy")
    if not isinstance(selector, Mapping):
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            "source profile.selector 必须是对象",
        )
    if not isinstance(capture_policy, Mapping):
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            "source profile.capture_policy 必须是对象",
        )
    if source_type == "meego":
        normalized_selector, normalized_policy = _validate_meego_v2(
            source_uid=source_uid,
            selector=selector,
            capture_policy=capture_policy,
        )
    elif source_type == "feishu_doc":
        normalized_selector, normalized_policy = _validate_feishu_doc_v2(
            source_uid=source_uid,
            selector=selector,
            capture_policy=capture_policy,
        )
    else:  # pragma: no cover - guarded above; keeps dispatch fail closed.
        raise SourceProfileError(
            "SOURCE_PROFILE_UNSUPPORTED",
            f"source profile v2 暂不支持 source_type={source_type}",
        )
    return {
        "schema_version": PROFILE_SCHEMA_V2,
        "source_type": source_type,
        "source_uid": source_uid,
        "source_url": source_url,
        "title": title,
        "selector": normalized_selector,
        "capture_policy": normalized_policy,
        "routine": _validate_routine(profile.get("routine")),
    }


def validate_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(profile, Mapping):
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            "source profile 顶层必须是 JSON 对象",
        )
    _reject_credentials(profile)
    schema_version = profile.get("schema_version")
    if schema_version == PROFILE_SCHEMA_V1:
        return _validate_v1_profile(profile)
    if schema_version == PROFILE_SCHEMA_V2:
        return _validate_v2_profile(profile)
    raise SourceProfileError(
        "SOURCE_PROFILE_INVALID",
        "source profile schema_version 必须是 "
        + " 或 ".join(sorted(PROFILE_SCHEMAS)),
    )


def profile_relative_path(profile: Mapping[str, Any]) -> Path:
    normalized = validate_profile(profile)
    digest = hashlib.sha256(normalized["source_uid"].encode("utf-8")).hexdigest()
    return Path("sources") / f"{normalized['source_type']}-{digest}.json"


def load_profile(kb: Path, source_uid: str) -> dict[str, Any]:
    source_uid = source_uid.strip()
    if not source_uid:
        raise SourceProfileError(
            "SOURCE_PROFILE_NOT_FOUND",
            "source_uid 不能为空",
        )
    digest = hashlib.sha256(source_uid.encode("utf-8")).hexdigest()
    root = kb.resolve() / "sources"
    paths = sorted(root.glob(f"*-{digest}.json")) if root.is_dir() else []
    if not paths:
        raise SourceProfileError(
            "SOURCE_PROFILE_NOT_FOUND",
            f"KB 中没有 source profile: {source_uid}",
        )
    matches: list[dict[str, Any]] = []
    for path in paths:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SourceProfileError(
                "SOURCE_PROFILE_INVALID",
                f"无法读取 source profile: {path}",
            ) from exc
        normalized = validate_profile(value)
        expected = profile_relative_path(normalized).name
        if path.name != expected:
            raise SourceProfileError(
                "SOURCE_PROFILE_PATH_MISMATCH",
                f"source profile 文件名与 source_uid 不一致: {path}",
            )
        if normalized["source_uid"] == source_uid:
            matches.append(normalized)
    if not matches:
        raise SourceProfileError(
            "SOURCE_PROFILE_NOT_FOUND",
            f"KB 中没有 source profile: {source_uid}",
        )
    if len(matches) != 1:
        raise SourceProfileError(
            "SOURCE_PROFILE_IDENTITY_MISMATCH",
            f"KB 中存在重复 source profile: {source_uid}",
        )
    return matches[0]


def list_profiles(
    kb: Path,
    *,
    source_type: str = "",
) -> list[dict[str, Any]]:
    source_type = source_type.strip()
    if source_type and source_type not in PROFILE_SOURCE_TYPES:
        raise SourceProfileError(
            "SOURCE_PROFILE_UNSUPPORTED",
            f"source profile 暂不支持 source_type={source_type}",
        )
    root = kb.resolve() / "sources"
    if not root.is_dir():
        return []
    result = []
    for path in sorted(root.glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SourceProfileError(
                "SOURCE_PROFILE_INVALID",
                f"无法读取 source profile: {path}",
            ) from exc
        normalized = validate_profile(value)
        expected = profile_relative_path(normalized).name
        if path.name != expected:
            raise SourceProfileError(
                "SOURCE_PROFILE_PATH_MISMATCH",
                f"source profile 文件名与 source_uid 不一致: {path}",
            )
        if source_type and normalized["source_type"] != source_type:
            continue
        result.append(normalized)
    return result


def _run_git(kb: Path, args: Sequence[str], *, check: bool = True):
    completed = subprocess.run(
        ["git", *args],
        cwd=kb,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and completed.returncode != 0:
        raise SourceProfileError(
            "SOURCE_PROFILE_GIT_ERROR",
            completed.stderr.strip() or completed.stdout.strip() or "git 命令失败",
        )
    return completed


def _dirty_paths(kb: Path) -> set[str]:
    output = _run_git(kb, ["status", "--porcelain=v1", "-z"]).stdout
    chunks = output.split("\0")
    paths: set[str] = set()
    index = 0
    while index < len(chunks):
        entry = chunks[index]
        if not entry:
            index += 1
            continue
        status = entry[:2]
        value = entry[3:]
        if status.startswith("R") or status.startswith("C"):
            index += 1
            if index < len(chunks):
                value = chunks[index]
        paths.add(value)
        index += 1
    return paths


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def _restore(path: Path, content: bytes | None) -> None:
    if content is None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    else:
        _atomic_write(path, content)


def save_profile(
    kb: Path,
    profile: Mapping[str, Any],
    *,
    skill_root: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Atomically save one KB profile, rebuild INDEX, and create a local commit."""
    kb = kb.resolve()
    skill_root = skill_root.resolve()
    normalized = validate_profile(profile)
    if not (kb / ".git").is_dir():
        raise SourceProfileError(
            "SOURCE_PROFILE_KB_INVALID",
            "知识库不是本地 Git 仓库，无法创建 source profile 回滚点",
        )
    if _run_git(kb, ["remote"]).stdout.splitlines():
        raise SourceProfileError(
            "SOURCE_PROFILE_KB_REMOTE",
            "知识库 Git 配置了 remote，拒绝写入机密 source profile",
        )
    if _run_git(kb, ["diff", "--cached", "--name-only"]).stdout.splitlines():
        raise SourceProfileError(
            "SOURCE_PROFILE_GIT_DIRTY",
            "知识库已有暂存变更，拒绝混入 source profile commit",
        )
    relative = profile_relative_path(normalized)
    target = kb / relative
    payload = _canonical_bytes(normalized) + b"\n"
    if target.is_file() and target.read_bytes() == payload:
        return {
            "status": "noop",
            "source_uid": normalized["source_uid"],
            "profile_path": str(relative),
            "profile_revision": profile_revision(normalized),
        }
    if now is None:
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
    index_path = kb / "INDEX.md"
    journal_path = (
        kb
        / "journal"
        / now.strftime("%Y-%m")
        / (now.strftime("%Y-%m-%d") + ".md")
    )
    target_paths = [target, index_path, journal_path]
    relative_paths = [str(path.relative_to(kb)) for path in target_paths]
    overlap = sorted(_dirty_paths(kb) & set(relative_paths))
    if overlap:
        raise SourceProfileError(
            "SOURCE_PROFILE_GIT_DIRTY",
            "source profile 目标已有未提交修改: " + ", ".join(overlap),
        )

    lock_path = kb / ".git" / "byteworker-source-profile.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if _run_git(kb, ["diff", "--cached", "--name-only"]).stdout.splitlines():
            raise SourceProfileError(
                "SOURCE_PROFILE_GIT_DIRTY",
                "获取锁后发现知识库已有暂存变更，拒绝继续",
            )
        overlap = sorted(_dirty_paths(kb) & set(relative_paths))
        if overlap:
            raise SourceProfileError(
                "SOURCE_PROFILE_GIT_DIRTY",
                "获取锁后发现 source profile 目标已有未提交修改: "
                + ", ".join(overlap),
            )
        snapshots = {
            path: path.read_bytes() if path.exists() else None
            for path in target_paths
        }
        git_index_path = kb / ".git" / "index"
        git_index_snapshot = (
            git_index_path.read_bytes() if git_index_path.exists() else None
        )
        staged = False
        try:
            _atomic_write(target, payload)
            rebuild = subprocess.run(
                [
                    sys.executable,
                    str(skill_root / "bin" / "rebuild_index.py"),
                    str(kb),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if rebuild.returncode != 0:
                raise SourceProfileError(
                    "SOURCE_PROFILE_INDEX_ERROR",
                    rebuild.stderr.strip()
                    or rebuild.stdout.strip()
                    or "INDEX 重建失败",
                )
            existing_journal = snapshots[journal_path]
            journal = (
                existing_journal.decode("utf-8")
                if existing_journal is not None
                else f"# {now:%Y-%m-%d}\n"
            )
            if journal and not journal.endswith("\n"):
                journal += "\n"
            title = normalized["title"].replace("\n", " ")
            journal += (
                f"- {now:%H:%M} source-profile {title} | "
                f"source_uid={normalized['source_uid']} | "
                f"revision={profile_revision(normalized)}\n"
            )
            _atomic_write(journal_path, journal.encode("utf-8"))
            check = _run_git(
                kb,
                ["diff", "--check", "--", *relative_paths],
                check=False,
            )
            if check.returncode != 0:
                raise SourceProfileError(
                    "SOURCE_PROFILE_GIT_ERROR",
                    check.stdout.strip() or check.stderr.strip(),
                )
            _run_git(kb, ["add", "--", *relative_paths])
            staged = True
            staged_paths = set(
                _run_git(
                    kb,
                    ["diff", "--cached", "--name-only"],
                ).stdout.splitlines()
            )
            unexpected = staged_paths - set(relative_paths)
            if unexpected:
                raise SourceProfileError(
                    "SOURCE_PROFILE_GIT_ERROR",
                    "暂存区出现事务外文件: " + ", ".join(sorted(unexpected)),
                )
            if not staged_paths:
                raise SourceProfileError(
                    "SOURCE_PROFILE_GIT_ERROR",
                    "source profile 事务没有产生可提交变更",
                )
            _run_git(
                kb,
                [
                    "commit",
                    "-m",
                    f"configure source {normalized['source_type']}",
                ],
            )
            commit = _run_git(kb, ["rev-parse", "HEAD"]).stdout.strip()
        except Exception:
            if staged:
                if git_index_snapshot is None:
                    try:
                        git_index_path.unlink()
                    except FileNotFoundError:
                        pass
                else:
                    _atomic_write(git_index_path, git_index_snapshot)
            for path, content in snapshots.items():
                _restore(path, content)
            raise
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return {
        "status": "committed",
        "source_uid": normalized["source_uid"],
        "profile_path": str(relative),
        "profile_revision": profile_revision(normalized),
        "routine": normalized["routine"],
        "commit": commit,
        "index_rebuilt": True,
        "journal": str(journal_path.relative_to(kb)),
    }
