"""Pure adapter from the existing Meego capture envelope to SourceBundle."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from .base import Capabilities
from ..models import (
    BUNDLE_SCHEMA,
    DEFAULT_SKILL_ROOT,
    SourceBundle,
    SourceBundleError,
    canonical_sha256,
)


CAPTURE_SCHEMA = "byteworker-source-capture/v1"
SNAPSHOT_SCHEMA = "byteworker-source-snapshot/v1"


def _walk_mappings(value: Any):
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk_mappings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_mappings(child)


def _first(value: Any, keys: Sequence[str]) -> str:
    for item in _walk_mappings(value):
        for key in keys:
            child = item.get(key)
            if child not in (None, ""):
                return str(child).strip()
    return ""


def _stable_id(record: Mapping[str, Any]) -> str:
    return _first(record, ("work_item_id", "workItemId", "id"))


def _title(record: Mapping[str, Any], fallback: str) -> str:
    return (
        _first(
            record,
            (
                "name",
                "title",
                "work_item_name",
                "workItemName",
                "summary",
                "subject",
            ),
        )
        or fallback
    )


def _source_time(record: Mapping[str, Any]) -> str:
    return _first(
        record,
        (
            "updated_at",
            "updated_time",
            "update_time",
            "modified_at",
            "modified_time",
        ),
    )


def _field_value(record: Mapping[str, Any], field: str) -> Any:
    for item in _walk_mappings(record):
        if field in item:
            return item[field]
    return None


def build_meego_bundle(
    capture: Mapping[str, Any],
    *,
    capture_path: Path,
    skill_root: Path = DEFAULT_SKILL_ROOT,
) -> SourceBundle:
    """Convert an already captured Meego envelope without fetching or writing."""

    if not isinstance(capture, Mapping):
        raise SourceBundleError(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Meego capture 顶层必须是对象",
            path="capture",
        )
    if capture.get("schema_version") != CAPTURE_SCHEMA:
        raise SourceBundleError(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            f"Meego capture schema_version 必须是 {CAPTURE_SCHEMA}",
            path="capture.schema_version",
        )
    if capture.get("source_type") != "meego":
        raise SourceBundleError(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Meego adapter 只接受 source_type=meego",
            path="capture.source_type",
        )
    snapshot = capture.get("snapshot")
    if not isinstance(snapshot, Mapping):
        raise SourceBundleError(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Meego capture 缺少 snapshot 对象",
            path="capture.snapshot",
        )
    if (
        snapshot.get("schema_version") != SNAPSHOT_SCHEMA
        or snapshot.get("source_type") != "meego"
    ):
        raise SourceBundleError(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Meego snapshot schema 或 source_type 非法",
            path="capture.snapshot",
        )
    source_uid = str(capture.get("source_uid", "")).strip()
    if not source_uid or snapshot.get("source_uid") != source_uid:
        raise SourceBundleError(
            "SOURCE_BUNDLE_IDENTITY_MISMATCH",
            "Meego capture 与 snapshot 的 source_uid 不一致",
            path="capture.source_uid",
        )
    pagination = capture.get("pagination")
    if not isinstance(pagination, Mapping) or pagination.get("complete") is not True:
        raise SourceBundleError(
            "SOURCE_BUNDLE_INCOMPLETE",
            "Meego capture 必须是完整分页结果",
            path="capture.pagination.complete",
        )
    records = snapshot.get("records")
    if not isinstance(records, list):
        raise SourceBundleError(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Meego snapshot.records 必须是数组",
            path="capture.snapshot.records",
        )
    snapshot_hash = canonical_sha256(snapshot)
    if capture.get("content_hash") != snapshot_hash:
        raise SourceBundleError(
            "SOURCE_BUNDLE_HASH_MISMATCH",
            "Meego capture.content_hash 与 canonical snapshot hash 不一致",
            path="capture.content_hash",
        )
    anchors = capture.get("anchors")
    if not isinstance(anchors, list):
        raise SourceBundleError(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Meego capture.anchors 必须是数组",
            path="capture.anchors",
        )
    anchor_ids = {
        str(anchor.get("anchor_id", "")).strip()
        for anchor in anchors
        if isinstance(anchor, Mapping)
    }
    anchors_by_id = {
        str(anchor.get("anchor_id", "")).strip(): anchor
        for anchor in anchors
        if isinstance(anchor, Mapping)
    }
    requested_fields = capture.get("requested_fields", [])
    if not isinstance(requested_fields, list):
        raise SourceBundleError(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Meego capture.requested_fields 必须是数组",
            path="capture.requested_fields",
        )
    record_index = []
    record_ids: set[str] = set()
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise SourceBundleError(
                "SOURCE_BUNDLE_ADAPTER_INVALID",
                f"Meego records[{index}] 必须是对象",
                path=f"capture.snapshot.records[{index}]",
            )
        record_id = _stable_id(record)
        if not record_id or record_id in record_ids:
            raise SourceBundleError(
                "SOURCE_BUNDLE_ADAPTER_INVALID",
                "Meego record 必须有唯一稳定 work_item_id",
                path=f"capture.snapshot.records[{index}]",
            )
        record_ids.add(record_id)
        anchor_id = f"workitem:{record_id}"
        if anchor_id not in anchor_ids:
            raise SourceBundleError(
                "SOURCE_BUNDLE_ADAPTER_INVALID",
                f"Meego record 缺少对应 anchor: {anchor_id}",
                path=f"capture.snapshot.records[{index}]",
            )
        item = {
            "record_id": record_id,
            "title": _title(record, record_id),
            "anchor_id": anchor_id,
            "locator": anchors_by_id[anchor_id].get("locator", {}),
            "fields": {
                str(field): _field_value(record, str(field))
                for field in requested_fields
                if str(field).strip()
            },
        }
        source_time = _source_time(record)
        if source_time:
            item["source_time"] = source_time
        record_index.append(item)

    bundle = {
        "schema_version": BUNDLE_SCHEMA,
        "identity": {
            "source_type": "meego",
            "source_uid": source_uid,
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
                "heading": "Meego 视图快照",
            }
        ],
        "coverage": {
            "status": "complete",
            "components": {"snapshot": "complete"},
        },
        "anchors": anchors,
        "provider_metadata": {
            "captured_at": capture.get("captured_at"),
            "coordinates": capture.get("coordinates", {}),
            "requested_fields": requested_fields,
            "pagination": pagination,
            "sanitization": capture.get("sanitization", {}),
            "source_profile": capture.get("source_profile", {}),
        },
        "record_index": record_index,
        "snapshot_hash": snapshot_hash,
        "payload_hash": None,
    }
    return SourceBundle.from_dict(bundle, skill_root=skill_root)


meego_capture_to_bundle = build_meego_bundle


def meego_bundle_to_transaction_source(bundle: SourceBundle) -> dict[str, Any]:
    """Materialize the existing digest-plan/v1 Meego source contract."""

    if bundle.identity.source_type != "meego":
        raise SourceBundleError(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Meego adapter 收到非 Meego bundle",
            path="identity.source_type",
        )
    metadata = bundle.provider_metadata
    coordinates = metadata.get("coordinates", {})
    fields = metadata.get("requested_fields", [])
    if not isinstance(coordinates, Mapping) or not isinstance(fields, list):
        raise SourceBundleError(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Meego bundle provider_metadata 缺少 coordinates/requested_fields",
            path="provider_metadata",
        )
    components = []
    for component in bundle.components:
        value = component.to_dict()
        value["coverage"] = bundle.coverage.components[component.name]
        components.append(value)
    source = {
        "type": "meego",
        "uid": bundle.identity.source_uid,
        "url": bundle.identity.source_url,
        "title": bundle.identity.title,
        "project_key": str(coordinates.get("project_key", "")).strip(),
        "view_id": str(coordinates.get("view_id", "")).strip(),
        "fields": list(fields),
        "components": components,
    }
    profile = metadata.get("source_profile")
    if isinstance(profile, Mapping):
        source["profile_path"] = str(profile.get("path", "")).strip()
        source["profile_revision"] = str(profile.get("revision", "")).strip()
    if bundle.record_index is not None:
        source["record_index"] = [
            item.to_dict() for item in bundle.record_index
        ]
    return source


class MeegoCaptureAdapter:
    source_type = "meego"
    capabilities = Capabilities(
        component_kinds=frozenset({"records"}),
        coverage_dimensions=frozenset({"snapshot"}),
        stable_record_ids=True,
        record_index=True,
        incremental_diff=True,
    )

    def build_bundle(self, **kwargs: Any) -> SourceBundle:
        return build_meego_bundle(**kwargs)

    def to_transaction_source(self, bundle: SourceBundle) -> dict[str, Any]:
        return meego_bundle_to_transaction_source(bundle)
