"""Shared validation and construction for file-backed SourceBundle requests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sources import (
    SourceBundle,
    SourceBundleError,
    create_default_registry,
    ensure_source_request_safe,
)


DEFAULT_SKILL_ROOT = Path(__file__).resolve().parents[1]


def build_bundle_from_request(
    source_type: str,
    request_path: Path,
    *,
    skill_root: Path = DEFAULT_SKILL_ROOT,
) -> SourceBundle:
    raw_argument = str(request_path).strip()
    if raw_argument.startswith(("{", "[")):
        raise SourceBundleError(
            "SOURCE_BUNDLE_REQUEST_INLINE_UNSUPPORTED",
            "source bundle request 只接受 JSON 文件路径，不接受内联 JSON",
            path="request",
            hint="先把 request JSON 写入系统临时目录或知识库目录，再传文件路径。",
        )
    resolved = request_path.expanduser().resolve()
    if not resolved.is_file():
        raise SourceBundleError(
            "SOURCE_BUNDLE_REQUEST_NOT_FOUND",
            f"bundle request JSON 文件不存在: {resolved}",
            path=str(resolved),
            hint=(
                f"运行 source bundle-spec --source-type {source_type} 查看契约，"
                "并把 request 写入临时文件。"
            ),
        )
    root = skill_root.expanduser().resolve()
    if resolved == root or root in resolved.parents:
        raise SourceBundleError(
            "SOURCE_BUNDLE_PATH_IN_SKILL_REPO",
            "业务 bundle request 不得位于 byteworker skill 仓库",
            path=str(resolved),
        )
    try:
        request: Any = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceBundleError(
            "SOURCE_BUNDLE_REQUEST_INVALID",
            f"无法读取 bundle request JSON: {resolved}",
            path=str(resolved),
        ) from exc
    if not isinstance(request, dict):
        raise SourceBundleError(
            "SOURCE_BUNDLE_REQUEST_INVALID",
            "bundle request 顶层必须是 JSON 对象",
            path=str(resolved),
        )
    if "source_type" in request:
        raise SourceBundleError(
            "SOURCE_BUNDLE_REQUEST_INVALID",
            "source_type 只能由调用参数声明，不得在 request 中重复",
            path="source_type",
        )
    if "capture" in request:
        raise SourceBundleError(
            "SOURCE_BUNDLE_REQUEST_INVALID",
            "bundle request 不接受内联 capture；请只提供 capture_path",
            path="capture",
        )
    ensure_source_request_safe(request)
    return create_default_registry().build_bundle(
        source_type,
        **request,
        skill_root=root,
    )
