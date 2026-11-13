"""Pure adapter for a captured Feishu document component set."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from .base import Capabilities
from ..models import (
    BUNDLE_SCHEMA,
    DEFAULT_SKILL_ROOT,
    SourceBundle,
    SourceBundleError,
    canonical_sha256,
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


def whiteboard_nodes(value: Any) -> Any:
    """Unwrap successful transport envelopes and retain the full node objects."""
    if isinstance(value, Mapping) and value.get("ok") is not False:
        data = value.get("data", value)
        if isinstance(data, Mapping) and isinstance(data.get("nodes"), list):
            return data["nodes"]
    return value


def _node_hashes(value: Any) -> dict[str, str] | None:
    value = whiteboard_nodes(value)
    if not isinstance(value, list):
        return None
    nodes = {}
    for node in value:
        node_id = node.get("id") if isinstance(node, dict) else None
        if not isinstance(node_id, str) or not node_id or node_id in nodes:
            return None
        nodes[node_id] = canonical_sha256(node)
    return nodes


def archived_whiteboards_match(
    fields: Mapping[str, Any], raw_body: str, components: Sequence[Any],
) -> bool:
    """Compare node arrays against the archived, hash-verified component bytes."""
    boards = {item.name: item for item in components if item.kind == "whiteboard"}
    stored = {}
    for entry in fields.get("payload_components", []):
        parts = str(entry).split("|")
        if len(parts) == 3 and parts[1] == "whiteboard":
            stored[parts[0]] = parts[2]
    if not boards or boards.keys() != stored.keys():
        return False
    stored_hashes = set(stored.values())
    archived = {}
    for block in re.finditer(r"^(`{3,})json[ \t]*\n(.*?)\n\1[ \t]*$", raw_body, re.M | re.S):
        content = block[2]
        for text in (content, content + "\n"):
            digest = "sha256:" + hashlib.sha256(text.encode()).hexdigest()
            if digest in stored_hashes:
                try:
                    archived[digest] = _node_hashes(json.loads(text))
                except ValueError:
                    return False
    for name, board in boards.items():
        if board.digest == stored[name]:
            continue
        previous = archived.get(stored[name])
        try:
            current = _node_hashes(json.loads(board.data))
        except ValueError:
            return False
        if previous is None or previous != current:
            return False
    return True


_RAW_COMPONENT = re.compile(r"^## [^\n]+\n\n`{3,}json[ \t]*$", re.M)
_BODY_TAG = re.compile(r"<[A-Za-z][^<>]*>")
_COMMENT_REFS = re.compile(r"""\s+comment-refs=(?:"[^"]*"|'[^']*')""")
_CHART_PREVIEW = re.compile(r"""(\s+thumbnail-url=")([^"]+)(")""")
_USER_CITE = re.compile(r"""(<cite\b[^<>]*?\buser-id=")(ou_[A-Za-z0-9]+)(")""")


def _stable_body(body: str, *, same_revision: bool) -> str:
    def normalize_preview(match: re.Match[str]) -> str:
        try:
            url = urlsplit(match[2])
        except ValueError:
            return match[0]
        if url.query.startswith("code=") and "&" not in url.query:
            return match[1] + url._replace(query="").geturl() + match[3]
        return match[0]

    def normalize_tag(match: re.Match[str]) -> str:
        tag = _COMMENT_REFS.sub("", match[0])
        if tag.startswith("<chart-refer-host-perm "):
            tag = _CHART_PREVIEW.sub(normalize_preview, tag)
        return tag

    body = _BODY_TAG.sub(normalize_tag, body)
    if same_revision:
        aliases: dict[str, int] = {}
        body = _USER_CITE.sub(
            lambda match: f"{match[1]}{aliases.setdefault(match[2], len(aliases))}{match[3]}", body,
        )
    return body


def archived_document_body_matches(
    fields: Mapping[str, Any], raw_body: str, components: Sequence[Any], *, revision: str,
) -> bool:
    """Use full comment snapshots to compare markup with transport metadata removed."""
    if not any(item.kind == "comments" and item.coverage in ("", "complete") for item in components):
        return False
    if fields.get("comments_status") != "complete":
        return False
    section = _RAW_COMPONENT.search(raw_body)
    if section is None:
        return False
    stored = raw_body[:section.start()].rstrip("\n")
    for body in (stored, stored + "\n"):
        if "sha256:" + hashlib.sha256(body.encode()).hexdigest() == fields.get("body_hash"):
            current = next(item.data.decode() for item in components if item.kind == "body")
            same_revision = bool(revision and fields.get("source_revision") == revision)
            return (
                _stable_body(body, same_revision=same_revision)
                == _stable_body(current, same_revision=same_revision)
            )
    return False


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
    root = skill_root.resolve()
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
        if component["mode"] == "canonical-json" and not component.get("json_pointer"):
            path = Path(str(component.get("path", ""))).expanduser()
            if path.is_absolute() and not path.resolve().is_relative_to(root) and path.is_file():
                try:
                    envelope = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    envelope = None
                if isinstance(whiteboard_nodes(envelope), list) and isinstance(envelope, dict):
                    component["json_pointer"] = "/data/nodes" if "data" in envelope else "/nodes"
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
