"""Pure adapter for a captured Feishu document component set."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from .base import Capabilities
from ..models import (
    BUNDLE_SCHEMA,
    DEFAULT_SKILL_ROOT,
    SourceBundle,
    SourceBundleError,
)


def _component(
    value: Mapping[str, Any],
    *,
    default_name: str,
    default_kind: str,
    default_mode: str,
    default_heading: str,
) -> tuple[dict[str, Any], str]:
    description = dict(value)
    coverage = str(description.pop("coverage", "complete")).strip()
    description.setdefault("name", default_name)
    description.setdefault("kind", default_kind)
    description.setdefault("mode", default_mode)
    description.setdefault("heading", default_heading)
    return description, coverage


def build_feishu_document_bundle(
    *,
    source_uid: str,
    source_url: str,
    title: str,
    revision: str,
    body: Mapping[str, Any],
    comments: Mapping[str, Any] | None = None,
    whiteboards: Sequence[Mapping[str, Any]] = (),
    anchors: Sequence[Mapping[str, Any]] = (),
    provider_metadata: Mapping[str, Any] | None = None,
    skill_root: Path = DEFAULT_SKILL_ROOT,
) -> SourceBundle:
    """Build a bundle from already fetched body/comments/whiteboard artifacts."""

    if str(source_uid).strip().startswith("feishu_doc:"):
        raise SourceBundleError(
            "SOURCE_BUNDLE_SOURCE_UID_INVALID",
            "feishu_doc source_uid 应直接使用 document_id 或 wiki token，"
            "不得添加 feishu_doc: 前缀",
            path="source_uid",
            hint="去掉 feishu_doc: 前缀后重试。",
        )
    body_component, body_coverage = _component(
        body,
        default_name="body",
        default_kind="body",
        default_mode="verbatim",
        default_heading="文档正文",
    )
    components = [body_component]
    coverage = {body_component["name"]: body_coverage}
    comments_coverage = "unavailable"
    if comments is not None:
        comments_component, comments_coverage = _component(
            comments,
            default_name="comments",
            default_kind="comments",
            default_mode="canonical-json",
            default_heading="文档评论",
        )
        if comments_coverage == "unavailable":
            raise SourceBundleError(
                "SOURCE_BUNDLE_ADAPTER_INVALID",
                "评论不可用时应省略 comments component，而不是提供伪组件",
                path="comments.coverage",
            )
        components.append(comments_component)
        coverage[comments_component["name"]] = comments_coverage
    for index, whiteboard in enumerate(whiteboards, start=1):
        component, status = _component(
            whiteboard,
            default_name=f"whiteboard:{index}",
            default_kind="whiteboard",
            default_mode="canonical-json",
            default_heading=f"白板 {index}",
        )
        components.append(component)
        coverage[component["name"]] = status

    material = [state for state in coverage.values() if state != "not_applicable"]
    if not material or all(state == "complete" for state in material):
        overall = "complete"
    elif all(state == "unavailable" for state in material):
        overall = "unavailable"
    else:
        overall = "partial"

    metadata = dict(provider_metadata or {})
    metadata.setdefault("comments_status", comments_coverage)
    metadata.setdefault("whiteboard_count", len(whiteboards))
    if whiteboards:
        whiteboard_states = [
            coverage[component["name"]]
            for component in components
            if component["kind"] == "whiteboard"
        ]
        metadata.setdefault(
            "whiteboards_status",
            (
                "complete"
                if all(state == "complete" for state in whiteboard_states)
                else "partial"
            ),
        )
    bundle = {
        "schema_version": BUNDLE_SCHEMA,
        "identity": {
            "source_type": "feishu_doc",
            "source_uid": source_uid,
            "source_url": source_url,
            "title": title,
            "revision": revision,
        },
        "components": components,
        "coverage": {
            "status": overall,
            "components": coverage,
        },
        "anchors": list(anchors),
        "provider_metadata": metadata,
        "snapshot_hash": None,
        "payload_hash": None,
    }
    return SourceBundle.from_dict(bundle, skill_root=skill_root)


feishu_document_to_bundle = build_feishu_document_bundle


def feishu_document_bundle_to_transaction_source(
    bundle: SourceBundle,
) -> dict[str, Any]:
    """Materialize the existing digest-plan/v1 Feishu document source."""

    if bundle.identity.source_type != "feishu_doc":
        raise SourceBundleError(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Feishu document adapter 收到其它类型 bundle",
            path="identity.source_type",
        )
    components = []
    whiteboard_states = []
    for component in bundle.components:
        value = component.to_dict()
        state = bundle.coverage.components[component.name]
        value["coverage"] = state
        components.append(value)
        if component.kind == "whiteboard":
            whiteboard_states.append(state)
    metadata = bundle.provider_metadata
    comments_component = next(
        (
            component
            for component in bundle.components
            if component.kind == "comments"
        ),
        None,
    )
    comments_status = str(
        metadata.get("comments_status")
        or (
            bundle.coverage.components.get(comments_component.name, "")
            if comments_component is not None
            else ""
        )
    ).strip()
    source: dict[str, Any] = {
        "type": "feishu_doc",
        "uid": bundle.identity.source_uid,
        "revision": bundle.identity.revision or "",
        "url": bundle.identity.source_url,
        "title": bundle.identity.title,
        "comments_status": comments_status,
        "components": components,
    }
    if metadata.get("comment_count") not in (None, ""):
        source["comment_count"] = metadata["comment_count"]
    for target, candidates in {
        "comment_reply_count": ("comment_reply_count", "reply_count"),
        "comments_latest_at": ("comments_latest_at",),
        "digest_period": ("digest_period", "period"),
    }.items():
        for candidate in candidates:
            if metadata.get(candidate) not in (None, ""):
                source[target] = metadata[candidate]
                break
    if whiteboard_states:
        source["whiteboards_status"] = str(
            metadata.get("whiteboards_status")
            or (
                "complete"
                if all(state == "complete" for state in whiteboard_states)
                else "partial"
            )
        ).strip()
    return source


class FeishuDocumentAdapter:
    source_type = "feishu_doc"
    request_builder = staticmethod(build_feishu_document_bundle)
    capabilities = Capabilities(
        component_kinds=frozenset({"body", "comments", "whiteboard"}),
        coverage_dimensions=frozenset({"body", "comments", "whiteboard"}),
    )

    def build_bundle(self, **kwargs: Any) -> SourceBundle:
        return build_feishu_document_bundle(**kwargs)

    def validate_bundle(self, bundle: SourceBundle) -> None:
        feishu_document_bundle_to_transaction_source(bundle)

    def to_transaction_source(self, bundle: SourceBundle) -> dict[str, Any]:
        return feishu_document_bundle_to_transaction_source(bundle)
