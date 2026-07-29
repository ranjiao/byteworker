"""Thin SourceBundle adapters for already-fetched text artifacts.

These adapters deliberately do not fetch remote content or copy local files.
They normalize existing UTF-8 transcript/body artifacts into the common
SourceBundle boundary and materialize the current digest-plan/v1 source shape.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .base import Capabilities
from ..models import (
    BUNDLE_SCHEMA,
    DEFAULT_SKILL_ROOT,
    SourceBundle,
    SourceBundleError,
)


LOCATOR_SCHEMA = "byteworker-source-locators/v1"


def _require_source_url(source_url: str, *, source_type: str) -> None:
    if not str(source_url).strip():
        raise SourceBundleError(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            f"{source_type} source_url 不得为空",
            path="source_url",
        )


def _component(
    value: Mapping[str, Any],
    *,
    default_name: str,
    default_heading: str,
    default_media_type: str,
) -> tuple[dict[str, Any], str]:
    if not isinstance(value, Mapping):
        raise SourceBundleError(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            f"{default_name} component 必须是对象",
            path=default_name,
        )
    component = dict(value)
    coverage = str(component.pop("coverage", "complete")).strip()
    component.setdefault("name", default_name)
    component.setdefault("kind", "body")
    component.setdefault("mode", "verbatim")
    component.setdefault("heading", default_heading)
    component.setdefault("media_type", default_media_type)
    if component.get("kind") != "body":
        raise SourceBundleError(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            f"{default_name} component.kind 必须是 body",
            path=f"{default_name}.kind",
        )
    if component.get("mode") != "verbatim":
        raise SourceBundleError(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            f"{default_name} component.mode 必须是 verbatim",
            path=f"{default_name}.mode",
        )
    return component, coverage


def _normalize_anchors(
    anchors: Sequence[Mapping[str, Any]],
    *,
    expected_kinds: frozenset[str],
    component_name: str,
) -> list[dict[str, Any]]:
    if not isinstance(anchors, (list, tuple)):
        raise SourceBundleError(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "anchors 必须是数组",
            path="anchors",
        )
    normalized: list[dict[str, Any]] = []
    for index, anchor in enumerate(anchors):
        if not isinstance(anchor, Mapping):
            raise SourceBundleError(
                "SOURCE_BUNDLE_ADAPTER_INVALID",
                f"anchors[{index}] 必须是对象",
                path=f"anchors[{index}]",
            )
        item = dict(anchor)
        kind = str(item.get("kind", "")).strip()
        if kind not in expected_kinds:
            raise SourceBundleError(
                "SOURCE_BUNDLE_ADAPTER_INVALID",
                f"anchors[{index}].kind={kind or 'missing'} 不属于该来源",
                path=f"anchors[{index}].kind",
            )
        item.setdefault("component", component_name)
        if item.get("component") != component_name:
            raise SourceBundleError(
                "SOURCE_BUNDLE_ADAPTER_INVALID",
                f"anchors[{index}] 必须指向 component={component_name}",
                path=f"anchors[{index}].component",
            )
        normalized.append(item)
    return normalized


def _read_locator_artifact(
    value: Mapping[str, Any] | Path,
    *,
    skill_root: Path,
) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    path = Path(value).expanduser().resolve(strict=False)
    root = skill_root.expanduser().resolve(strict=False)
    if path == root or root in path.parents:
        raise SourceBundleError(
            "SOURCE_BUNDLE_PATH_IN_SKILL_REPO",
            "业务 locator artifact 不得位于 byteworker skill 仓库",
            path=str(path),
        )
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SourceBundleError(
            "SOURCE_BUNDLE_READ_FAILED",
            f"无法读取 locator artifact: {path}",
            path=str(path),
        ) from exc
    if not isinstance(artifact, Mapping):
        raise SourceBundleError(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "locator artifact 顶层必须是对象",
            path=str(path),
        )
    return artifact


def _chat_anchors(
    *,
    source_uid: str,
    source_window: str,
    anchors: Sequence[Mapping[str, Any]],
    locator_artifact: Mapping[str, Any] | Path | None,
    component_name: str,
    skill_root: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw_anchors: Sequence[Mapping[str, Any]] = anchors
    locator_metadata: dict[str, Any] = {}
    if locator_artifact is not None:
        if anchors:
            raise SourceBundleError(
                "SOURCE_BUNDLE_ADAPTER_INVALID",
                "anchors 与 locator_artifact 只能提供一种",
                path="anchors",
            )
        artifact = _read_locator_artifact(
            locator_artifact,
            skill_root=skill_root,
        )
        if artifact.get("schema_version") != LOCATOR_SCHEMA:
            raise SourceBundleError(
                "SOURCE_BUNDLE_ADAPTER_INVALID",
                f"locator artifact schema_version 必须是 {LOCATOR_SCHEMA}",
                path="locator_artifact.schema_version",
            )
        if artifact.get("source_type") != "feishu_chat":
            raise SourceBundleError(
                "SOURCE_BUNDLE_IDENTITY_MISMATCH",
                "locator artifact source_type 不是 feishu_chat",
                path="locator_artifact.source_type",
            )
        if str(artifact.get("source_chat_id", "")).strip() != source_uid:
            raise SourceBundleError(
                "SOURCE_BUNDLE_IDENTITY_MISMATCH",
                "locator artifact 的 source_chat_id 与 source_uid 不一致",
                path="locator_artifact.source_chat_id",
            )
        if str(artifact.get("source_window", "")).strip() != source_window:
            raise SourceBundleError(
                "SOURCE_BUNDLE_IDENTITY_MISMATCH",
                "locator artifact 的 source_window 与本次窗口不一致",
                path="locator_artifact.source_window",
            )
        raw_anchors_value = artifact.get("anchors")
        if not isinstance(raw_anchors_value, list):
            raise SourceBundleError(
                "SOURCE_BUNDLE_ADAPTER_INVALID",
                "locator artifact.anchors 必须是数组",
                path="locator_artifact.anchors",
            )
        raw_anchors = raw_anchors_value
        locator_metadata = {
            "locator_schema": LOCATOR_SCHEMA,
            "anchor_count": len(raw_anchors_value),
        }
    return (
        _normalize_anchors(
            raw_anchors,
            expected_kinds=frozenset({"chat_message", "chat_thread"}),
            component_name=component_name,
        ),
        locator_metadata,
    )


def _build_text_bundle(
    *,
    source_type: str,
    source_uid: str,
    source_url: str,
    title: str,
    revision: str | None,
    component: Mapping[str, Any],
    coverage: str,
    anchors: Sequence[Mapping[str, Any]],
    provider_metadata: Mapping[str, Any] | None,
    skill_root: Path,
) -> SourceBundle:
    bundle = {
        "schema_version": BUNDLE_SCHEMA,
        "identity": {
            "source_type": source_type,
            "source_uid": source_uid,
            "source_url": source_url,
            "title": title,
            **({"revision": revision} if str(revision or "").strip() else {}),
        },
        "components": [dict(component)],
        "coverage": {
            "status": coverage,
            "components": {str(component["name"]): coverage},
        },
        "anchors": list(anchors),
        "provider_metadata": dict(provider_metadata or {}),
        "snapshot_hash": None,
        "payload_hash": None,
    }
    return SourceBundle.from_dict(bundle, skill_root=skill_root)


def _text_bundle_to_transaction_source(
    bundle: SourceBundle,
    *,
    expected_source_type: str,
    required_metadata: Sequence[str] = (),
) -> dict[str, Any]:
    if bundle.identity.source_type != expected_source_type:
        raise SourceBundleError(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            f"{expected_source_type} adapter 收到其它类型 bundle",
            path="identity.source_type",
        )
    metadata = bundle.provider_metadata
    source: dict[str, Any] = {
        "type": expected_source_type,
        "uid": bundle.identity.source_uid,
        "url": bundle.identity.source_url,
        "title": bundle.identity.title,
        "components": [],
    }
    if bundle.identity.revision:
        source["revision"] = bundle.identity.revision
    for field in required_metadata:
        value = str(metadata.get(field, "")).strip()
        if not value:
            raise SourceBundleError(
                "SOURCE_BUNDLE_ADAPTER_INVALID",
                f"{expected_source_type} bundle 缺少 provider_metadata.{field}",
                path=f"provider_metadata.{field}",
            )
        source[field] = value
    for component in bundle.components:
        value = component.to_dict()
        value["coverage"] = bundle.coverage.components[component.name]
        source["components"].append(value)
    return source


def build_feishu_chat_bundle(
    *,
    source_uid: str,
    title: str,
    source_window: str,
    transcript: Mapping[str, Any],
    source_url: str = "",
    revision: str | None = None,
    anchors: Sequence[Mapping[str, Any]] = (),
    locator_artifact: Mapping[str, Any] | Path | None = None,
    provider_metadata: Mapping[str, Any] | None = None,
    skill_root: Path = DEFAULT_SKILL_ROOT,
) -> SourceBundle:
    """Normalize a completed chat transcript and optional locator artifact."""
    source_window = str(source_window).strip()
    if not source_window:
        raise SourceBundleError(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "feishu_chat source_window 不得为空",
            path="source_window",
        )
    component, coverage = _component(
        transcript,
        default_name="body",
        default_heading="群聊逐字记录",
        default_media_type="text/plain",
    )
    normalized_anchors, locator_metadata = _chat_anchors(
        source_uid=source_uid,
        source_window=source_window,
        anchors=anchors,
        locator_artifact=locator_artifact,
        component_name=str(component["name"]),
        skill_root=skill_root,
    )
    metadata = dict(provider_metadata or {})
    metadata.update(locator_metadata)
    metadata["source_window"] = source_window
    return _build_text_bundle(
        source_type="feishu_chat",
        source_uid=source_uid,
        source_url=source_url,
        title=title,
        revision=revision,
        component=component,
        coverage=coverage,
        anchors=normalized_anchors,
        provider_metadata=metadata,
        skill_root=skill_root,
    )


def feishu_chat_bundle_to_transaction_source(
    bundle: SourceBundle,
) -> dict[str, Any]:
    return _text_bundle_to_transaction_source(
        bundle,
        expected_source_type="feishu_chat",
        required_metadata=("source_window",),
    )


def build_feishu_minutes_bundle(
    *,
    source_uid: str,
    source_url: str,
    title: str,
    transcript: Mapping[str, Any],
    revision: str | None = None,
    anchors: Sequence[Mapping[str, Any]] = (),
    provider_metadata: Mapping[str, Any] | None = None,
    skill_root: Path = DEFAULT_SKILL_ROOT,
) -> SourceBundle:
    """Normalize an already-fetched minutes transcript with segment anchors."""
    _require_source_url(source_url, source_type="feishu_minutes")
    component, coverage = _component(
        transcript,
        default_name="body",
        default_heading="妙记逐字稿",
        default_media_type="text/plain",
    )
    normalized_anchors = _normalize_anchors(
        anchors,
        expected_kinds=frozenset({"minutes_segment"}),
        component_name=str(component["name"]),
    )
    return _build_text_bundle(
        source_type="feishu_minutes",
        source_uid=source_uid,
        source_url=source_url,
        title=title,
        revision=revision,
        component=component,
        coverage=coverage,
        anchors=normalized_anchors,
        provider_metadata=provider_metadata,
        skill_root=skill_root,
    )


def feishu_minutes_bundle_to_transaction_source(
    bundle: SourceBundle,
) -> dict[str, Any]:
    return _text_bundle_to_transaction_source(
        bundle,
        expected_source_type="feishu_minutes",
    )


def build_web_bundle(
    *,
    source_uid: str,
    source_url: str,
    title: str,
    body: Mapping[str, Any],
    revision: str | None = None,
    anchors: Sequence[Mapping[str, Any]] = (),
    provider_metadata: Mapping[str, Any] | None = None,
    skill_root: Path = DEFAULT_SKILL_ROOT,
) -> SourceBundle:
    """Normalize an already-fetched web article body."""
    _require_source_url(source_url, source_type="web")
    component, coverage = _component(
        body,
        default_name="body",
        default_heading="网页正文",
        default_media_type="text/markdown",
    )
    normalized_anchors = _normalize_anchors(
        anchors,
        expected_kinds=frozenset({"web_section"}),
        component_name=str(component["name"]),
    )
    return _build_text_bundle(
        source_type="web",
        source_uid=source_uid,
        source_url=source_url,
        title=title,
        revision=revision,
        component=component,
        coverage=coverage,
        anchors=normalized_anchors,
        provider_metadata=provider_metadata,
        skill_root=skill_root,
    )


def web_bundle_to_transaction_source(bundle: SourceBundle) -> dict[str, Any]:
    return _text_bundle_to_transaction_source(
        bundle,
        expected_source_type="web",
    )


def build_local_markdown_bundle(
    *,
    source_uid: str,
    title: str,
    local_file: Mapping[str, Any],
    revision: str | None = None,
    anchors: Sequence[Mapping[str, Any]] = (),
    provider_metadata: Mapping[str, Any] | None = None,
    skill_root: Path = DEFAULT_SKILL_ROOT,
) -> SourceBundle:
    """Normalize a local Markdown file without copying or rewriting it."""
    component, coverage = _component(
        local_file,
        default_name="body",
        default_heading="本地原文",
        default_media_type="text/markdown",
    )
    normalized_anchors = _normalize_anchors(
        anchors,
        expected_kinds=frozenset({"local_span"}),
        component_name=str(component["name"]),
    )
    metadata = dict(provider_metadata or {})
    metadata["source_path"] = str(
        Path(str(component.get("path", ""))).expanduser().resolve(strict=False)
    )
    return _build_text_bundle(
        source_type="local_md",
        source_uid=source_uid,
        source_url="",
        title=title,
        revision=revision,
        component=component,
        coverage=coverage,
        anchors=normalized_anchors,
        provider_metadata=metadata,
        skill_root=skill_root,
    )


def local_markdown_bundle_to_transaction_source(
    bundle: SourceBundle,
) -> dict[str, Any]:
    return _text_bundle_to_transaction_source(
        bundle,
        expected_source_type="local_md",
    )


class FeishuChatAdapter:
    source_type = "feishu_chat"
    request_builder = staticmethod(build_feishu_chat_bundle)
    capabilities = Capabilities(
        component_kinds=frozenset({"body"}),
        coverage_dimensions=frozenset({"transcript"}),
    )

    def build_bundle(self, **kwargs: Any) -> SourceBundle:
        return build_feishu_chat_bundle(**kwargs)

    def validate_bundle(self, bundle: SourceBundle) -> None:
        feishu_chat_bundle_to_transaction_source(bundle)

    def to_transaction_source(self, bundle: SourceBundle) -> dict[str, Any]:
        return feishu_chat_bundle_to_transaction_source(bundle)


class FeishuMinutesAdapter:
    source_type = "feishu_minutes"
    request_builder = staticmethod(build_feishu_minutes_bundle)
    capabilities = Capabilities(
        component_kinds=frozenset({"body"}),
        coverage_dimensions=frozenset({"transcript"}),
    )

    def build_bundle(self, **kwargs: Any) -> SourceBundle:
        return build_feishu_minutes_bundle(**kwargs)

    def validate_bundle(self, bundle: SourceBundle) -> None:
        feishu_minutes_bundle_to_transaction_source(bundle)

    def to_transaction_source(self, bundle: SourceBundle) -> dict[str, Any]:
        return feishu_minutes_bundle_to_transaction_source(bundle)


class WebAdapter:
    source_type = "web"
    request_builder = staticmethod(build_web_bundle)
    capabilities = Capabilities(
        component_kinds=frozenset({"body"}),
        coverage_dimensions=frozenset({"body"}),
    )

    def build_bundle(self, **kwargs: Any) -> SourceBundle:
        return build_web_bundle(**kwargs)

    def validate_bundle(self, bundle: SourceBundle) -> None:
        web_bundle_to_transaction_source(bundle)

    def to_transaction_source(self, bundle: SourceBundle) -> dict[str, Any]:
        return web_bundle_to_transaction_source(bundle)


class LocalMarkdownAdapter:
    source_type = "local_md"
    request_builder = staticmethod(build_local_markdown_bundle)
    capabilities = Capabilities(
        component_kinds=frozenset({"body"}),
        coverage_dimensions=frozenset({"body"}),
    )

    def build_bundle(self, **kwargs: Any) -> SourceBundle:
        return build_local_markdown_bundle(**kwargs)

    def validate_bundle(self, bundle: SourceBundle) -> None:
        local_markdown_bundle_to_transaction_source(bundle)

    def to_transaction_source(self, bundle: SourceBundle) -> dict[str, Any]:
        return local_markdown_bundle_to_transaction_source(bundle)
