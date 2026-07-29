"""Pure adapter from a capture-v1 Feishu Base snapshot to SourceBundle."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .base import Capabilities
from .conformance import (
    require_rebuilt_bundle_match,
    structured_capture_path,
)
from ..models import (
    BUNDLE_SCHEMA,
    DEFAULT_SKILL_ROOT,
    SourceBundle,
    SourceBundleError,
    canonical_sha256,
)
from ..record_projection import project_legacy_record


CAPTURE_SCHEMA = "byteworker-source-capture/v1"
SNAPSHOT_SCHEMA = "byteworker-source-snapshot/v1"


def _fail(code: str, message: str, path: str) -> None:
    raise SourceBundleError(code, message, path=path)


def _capture_value(
    capture: Mapping[str, Any] | None,
    *,
    capture_path: Path,
    skill_root: Path,
) -> Mapping[str, Any]:
    if capture is not None:
        return capture
    path = Path(capture_path).expanduser()
    if not path.is_absolute():
        _fail(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Base capture_path 必须是绝对路径",
            "capture_path",
        )
    resolved = path.resolve(strict=False)
    root = skill_root.expanduser().resolve(strict=False)
    if resolved == root or root in resolved.parents:
        _fail(
            "SOURCE_BUNDLE_PATH_IN_SKILL_REPO",
            "Base capture 不得位于 byteworker skill 仓库",
            "capture_path",
        )
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceBundleError(
            "SOURCE_BUNDLE_READ_FAILED",
            f"无法读取 Base capture: {resolved}",
            path="capture_path",
        ) from exc
    if not isinstance(value, Mapping):
        _fail(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Base capture 顶层必须是对象",
            "capture",
        )
    return value


def _anchor_index(anchors: Any) -> dict[str, Mapping[str, Any]]:
    if not isinstance(anchors, list):
        _fail(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Base capture.anchors 必须是数组",
            "capture.anchors",
        )
    result: dict[str, Mapping[str, Any]] = {}
    for index, anchor in enumerate(anchors):
        if not isinstance(anchor, Mapping):
            _fail(
                "SOURCE_BUNDLE_ADAPTER_INVALID",
                f"Base anchors[{index}] 必须是对象",
                f"capture.anchors[{index}]",
            )
        anchor_id = str(anchor.get("anchor_id", "")).strip()
        if not anchor_id or anchor_id in result:
            _fail(
                "SOURCE_BUNDLE_ADAPTER_INVALID",
                "Base anchor_id 必须非空且唯一",
                f"capture.anchors[{index}].anchor_id",
            )
        result[anchor_id] = anchor
    return result


def _record_fields(record: Mapping[str, Any]) -> Mapping[str, Any] | None:
    fields = record.get("fields")
    if isinstance(fields, Mapping):
        return fields
    wrapper = record.get("record")
    if isinstance(wrapper, Mapping) and isinstance(wrapper.get("fields"), Mapping):
        return wrapper["fields"]
    return None


def _validate_record_anchor(
    *,
    anchor: Mapping[str, Any],
    record_id: str,
    coordinates: Mapping[str, Any],
    path: str,
) -> Mapping[str, Any]:
    if anchor.get("kind") != "base_record":
        _fail(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            f"Base record {record_id} 的 anchor kind 必须是 base_record",
            path,
        )
    locator = anchor.get("locator")
    if not isinstance(locator, Mapping):
        _fail(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            f"Base record {record_id} 的 anchor 缺少 locator",
            f"{path}.locator",
        )
    expected = {
        "base_token": coordinates["base_token"],
        "table_id": coordinates["table_id"],
        "view_id": coordinates["view_id"],
        "record_id": record_id,
    }
    if any(str(locator.get(key, "")) != str(value) for key, value in expected.items()):
        _fail(
            "SOURCE_BUNDLE_IDENTITY_MISMATCH",
            f"Base record {record_id} 的 anchor locator 与 snapshot 不一致",
            f"{path}.locator",
        )
    return locator


def build_feishu_base_bundle(
    capture: Mapping[str, Any] | None = None,
    *,
    capture_path: Path,
    skill_root: Path = DEFAULT_SKILL_ROOT,
) -> SourceBundle:
    """Convert an existing Base capture without fetching or writing."""

    capture = _capture_value(
        capture,
        capture_path=capture_path,
        skill_root=skill_root,
    )
    if not isinstance(capture, Mapping):
        _fail(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Base capture 顶层必须是对象",
            "capture",
        )
    if capture.get("schema_version") != CAPTURE_SCHEMA:
        _fail(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            f"Base capture schema_version 必须是 {CAPTURE_SCHEMA}",
            "capture.schema_version",
        )
    if (
        capture.get("capture_mode") != "snapshot"
        or capture.get("source_type") != "feishu_base"
    ):
        _fail(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Base adapter 只接受 capture_mode=snapshot、source_type=feishu_base",
            "capture.source_type",
        )
    snapshot = capture.get("snapshot")
    if not isinstance(snapshot, Mapping):
        _fail(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Base capture 缺少 snapshot 对象",
            "capture.snapshot",
        )
    if (
        snapshot.get("schema_version") != SNAPSHOT_SCHEMA
        or snapshot.get("source_type") != "feishu_base"
    ):
        _fail(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Base snapshot schema 或 source_type 非法",
            "capture.snapshot",
        )
    coordinates = capture.get("coordinates")
    snapshot_coordinates = snapshot.get("coordinates")
    if not isinstance(coordinates, Mapping) or snapshot_coordinates != coordinates:
        _fail(
            "SOURCE_BUNDLE_IDENTITY_MISMATCH",
            "Base capture 与 snapshot 的 coordinates 不一致",
            "capture.coordinates",
        )
    required_coordinates = ("base_token", "table_id", "view_id")
    if any(not str(coordinates.get(key, "")).strip() for key in required_coordinates):
        _fail(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Base coordinates 缺少 base_token/table_id/view_id",
            "capture.coordinates",
        )
    expected_uid = (
        f"feishu_base:{coordinates['base_token']}:"
        f"{coordinates['table_id']}:{coordinates['view_id']}"
    )
    if (
        capture.get("source_uid") != expected_uid
        or snapshot.get("source_uid") != expected_uid
    ):
        _fail(
            "SOURCE_BUNDLE_IDENTITY_MISMATCH",
            "Base source_uid 与 coordinates 不一致",
            "capture.source_uid",
        )
    pagination = capture.get("pagination")
    if not isinstance(pagination, Mapping) or pagination.get("complete") is not True:
        _fail(
            "SOURCE_BUNDLE_INCOMPLETE",
            "Base capture 必须是完整分页结果",
            "capture.pagination.complete",
        )
    records = snapshot.get("records")
    if not isinstance(records, list):
        _fail(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Base snapshot.records 必须是数组",
            "capture.snapshot.records",
        )
    if pagination.get("item_count") not in (None, len(records)):
        _fail(
            "SOURCE_BUNDLE_INCOMPLETE",
            "Base pagination.item_count 与 snapshot.records 不一致",
            "capture.pagination.item_count",
        )
    field_schema = snapshot.get("fields")
    requested_fields = capture.get("requested_fields")
    if not isinstance(field_schema, list) or not isinstance(requested_fields, list):
        _fail(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Base capture 缺少 fields/requested_fields 数组",
            "capture.requested_fields",
        )
    if not requested_fields:
        _fail(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Base requested_fields 不得为空",
            "capture.requested_fields",
        )
    snapshot_hash = canonical_sha256(snapshot)
    if capture.get("content_hash") != snapshot_hash:
        _fail(
            "SOURCE_BUNDLE_HASH_MISMATCH",
            "Base capture.content_hash 与 canonical snapshot hash 不一致",
            "capture.content_hash",
        )
    anchors = _anchor_index(capture.get("anchors"))
    record_index: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, record in enumerate(records):
        path = f"capture.snapshot.records[{index}]"
        if not isinstance(record, Mapping):
            _fail(
                "SOURCE_BUNDLE_ADAPTER_INVALID",
                f"Base records[{index}] 必须是对象",
                path,
            )
        projection = project_legacy_record(record, "feishu_base")
        record_id = str(projection.get("record_id", "")).strip()
        if not record_id or record_id in seen:
            _fail(
                "SOURCE_BUNDLE_ADAPTER_INVALID",
                "Base record 必须有唯一稳定 record_id",
                path,
            )
        seen.add(record_id)
        anchor_id = f"record:{record_id}"
        anchor = anchors.get(anchor_id)
        if anchor is None:
            _fail(
                "SOURCE_BUNDLE_ADAPTER_INVALID",
                f"Base record 缺少对应 anchor: {anchor_id}",
                path,
            )
        locator = _validate_record_anchor(
            anchor=anchor,
            record_id=record_id,
            coordinates=coordinates,
            path=f"capture.anchors.{anchor_id}",
        )
        fields = _record_fields(record)
        if fields is None:
            _fail(
                "SOURCE_BUNDLE_ADAPTER_INVALID",
                f"Base record {record_id} 缺少 fields 对象",
                f"{path}.fields",
            )
        title_candidates = projection.get("title_candidates", [])
        title = (
            str(title_candidates[0][1]).strip()
            if title_candidates
            else str(anchor.get("label", "")).strip() or record_id
        )
        item: dict[str, Any] = {
            "record_id": record_id,
            "title": title,
            "anchor_id": anchor_id,
            "locator": dict(locator),
            "fields": dict(fields),
        }
        source_time = str(anchor.get("source_time", "")).strip()
        if source_time:
            item["source_time"] = source_time
        record_index.append(item)
    record_index.sort(key=lambda item: item["record_id"])

    profile = capture.get("source_profile", {})
    if profile not in ({}, None) and not isinstance(profile, Mapping):
        _fail(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Base source_profile 必须是对象",
            "capture.source_profile",
        )
    bundle = {
        "schema_version": BUNDLE_SCHEMA,
        "identity": {
            "source_type": "feishu_base",
            "source_uid": expected_uid,
            "source_url": str(capture.get("source_url", "")).strip(),
            "title": str(capture.get("title", "")).strip(),
        },
        "components": [
            {
                "name": "snapshot",
                "kind": "records",
                "path": str(Path(capture_path).expanduser().resolve(strict=False)),
                "json_pointer": "/snapshot",
                "mode": "canonical-json",
                "heading": "飞书多维表格视图快照",
            }
        ],
        "coverage": {
            "status": "complete",
            "components": {"snapshot": "complete"},
        },
        "anchors": [
            dict(anchors[anchor_id]) for anchor_id in sorted(anchors)
        ],
        "provider_metadata": {
            "captured_at": capture.get("captured_at"),
            "coordinates": dict(coordinates),
            "requested_fields": list(requested_fields),
            "field_schema": list(field_schema),
            "pagination": dict(pagination),
            "sanitization": capture.get("sanitization", {}),
            "source_profile": dict(profile or {}),
        },
        "record_index": record_index,
        "snapshot_hash": snapshot_hash,
        "payload_hash": None,
    }
    return SourceBundle.from_dict(bundle, skill_root=skill_root)


feishu_base_capture_to_bundle = build_feishu_base_bundle


def feishu_base_bundle_to_transaction_source(
    bundle: SourceBundle,
) -> dict[str, Any]:
    """Materialize the current digest-plan/v1 Base source contract."""

    if bundle.identity.source_type != "feishu_base":
        _fail(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Base adapter 收到其它类型 bundle",
            "identity.source_type",
        )
    metadata = bundle.provider_metadata
    coordinates = metadata.get("coordinates")
    fields = metadata.get("requested_fields")
    if not isinstance(coordinates, Mapping) or not isinstance(fields, list) or not fields:
        _fail(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Base bundle provider_metadata 缺少 coordinates/requested_fields",
            "provider_metadata",
        )
    components = []
    for component in bundle.components:
        value = component.to_dict()
        value["coverage"] = bundle.coverage.components[component.name]
        components.append(value)
    source: dict[str, Any] = {
        "type": "feishu_base",
        "uid": bundle.identity.source_uid,
        "url": bundle.identity.source_url,
        "title": bundle.identity.title,
        "base_token": str(coordinates.get("base_token", "")).strip(),
        "table_id": str(coordinates.get("table_id", "")).strip(),
        "view_id": str(coordinates.get("view_id", "")).strip(),
        "fields": list(fields),
        "components": components,
    }
    profile = metadata.get("source_profile")
    if isinstance(profile, Mapping) and profile:
        source["profile_path"] = str(profile.get("path", "")).strip()
        source["profile_revision"] = str(profile.get("revision", "")).strip()
    if bundle.record_index is not None:
        source["record_index"] = [item.to_dict() for item in bundle.record_index]
    return source


class FeishuBaseCaptureAdapter:
    source_type = "feishu_base"
    request_builder = staticmethod(build_feishu_base_bundle)
    capabilities = Capabilities(
        component_kinds=frozenset({"records"}),
        coverage_dimensions=frozenset({"snapshot"}),
        stable_record_ids=True,
        record_index=True,
        incremental_diff=True,
    )

    def build_bundle(self, **kwargs: Any) -> SourceBundle:
        return build_feishu_base_bundle(**kwargs)

    def validate_bundle(self, bundle: SourceBundle) -> None:
        capture_path = structured_capture_path(
            bundle,
            source_type=self.source_type,
        )
        rebuilt = build_feishu_base_bundle(capture_path=capture_path)
        require_rebuilt_bundle_match(
            bundle,
            rebuilt,
            source_type=self.source_type,
        )

    def to_transaction_source(self, bundle: SourceBundle) -> dict[str, Any]:
        return feishu_base_bundle_to_transaction_source(bundle)
