"""Pure adapter from a capture-v1 Aeolus snapshot to SourceBundle."""

from __future__ import annotations

import json
import re
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
    SHA_RE,
    SourceBundle,
    SourceBundleError,
    canonical_sha256,
)
from ..record_projection import project_legacy_record


CAPTURE_SCHEMA = "byteworker-source-capture/v1"
SNAPSHOT_SCHEMA = "byteworker-source-snapshot/v1"
PROFILE_PATH_RE = re.compile(r"^sources/aeolus-[^/]+\.json$")
FILTER_MODES = {"dashboard", "explicit", "merge"}


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
            "Aeolus capture_path 必须是绝对路径",
            "capture_path",
        )
    resolved = path.resolve(strict=False)
    root = skill_root.expanduser().resolve(strict=False)
    if resolved == root or root in resolved.parents:
        _fail(
            "SOURCE_BUNDLE_PATH_IN_SKILL_REPO",
            "Aeolus capture 不得位于 byteworker skill 仓库",
            "capture_path",
        )
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceBundleError(
            "SOURCE_BUNDLE_READ_FAILED",
            f"无法读取 Aeolus capture: {resolved}",
            path="capture_path",
        ) from exc
    if not isinstance(value, Mapping):
        _fail(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Aeolus capture 顶层必须是对象",
            "capture",
        )
    return value


def _anchor_index(anchors: Any) -> dict[str, Mapping[str, Any]]:
    if not isinstance(anchors, list):
        _fail(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Aeolus capture.anchors 必须是数组",
            "capture.anchors",
        )
    result: dict[str, Mapping[str, Any]] = {}
    for index, anchor in enumerate(anchors):
        if not isinstance(anchor, Mapping):
            _fail(
                "SOURCE_BUNDLE_ADAPTER_INVALID",
                f"Aeolus anchors[{index}] 必须是对象",
                f"capture.anchors[{index}]",
            )
        anchor_id = str(anchor.get("anchor_id", "")).strip()
        if not anchor_id or anchor_id in result:
            _fail(
                "SOURCE_BUNDLE_ADAPTER_INVALID",
                "Aeolus anchor_id 必须非空且唯一",
                f"capture.anchors[{index}].anchor_id",
            )
        result[anchor_id] = anchor
    return result


def _expected_uid(coordinates: Mapping[str, Any]) -> str:
    keys = ("region", "app_id", "dashboard_id", "sheet_id")
    if any(not str(coordinates.get(key, "")).strip() for key in keys):
        _fail(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Aeolus coordinates 缺少 region/app_id/dashboard_id/sheet_id",
            "capture.coordinates",
        )
    return (
        f"aeolus:{coordinates['region']}:{coordinates['app_id']}:"
        f"{coordinates['dashboard_id']}:{coordinates['sheet_id']}"
    )


def _validate_record_anchor(
    *,
    anchor: Mapping[str, Any],
    record: Mapping[str, Any],
    coordinates: Mapping[str, Any],
    path: str,
) -> Mapping[str, Any]:
    if anchor.get("kind") != "aeolus_report":
        _fail(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Aeolus report anchor kind 必须是 aeolus_report",
            path,
        )
    locator = anchor.get("locator")
    if not isinstance(locator, Mapping):
        _fail(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Aeolus report anchor 缺少 locator",
            f"{path}.locator",
        )
    expected = {
        **{key: coordinates[key] for key in coordinates},
        "report_id": record.get("report_id"),
        "dataset_id": record.get("dataset_id"),
        "effective_filters": record.get("effective_filters"),
    }
    if any(locator.get(key) != value for key, value in expected.items()):
        _fail(
            "SOURCE_BUNDLE_IDENTITY_MISMATCH",
            "Aeolus report anchor locator 与 snapshot 不一致",
            f"{path}.locator",
        )
    return locator


def _record_source_time(record: Mapping[str, Any]) -> str:
    for key in ("source_time", "updated_at", "updated_time"):
        value = record.get(key)
        if value not in (None, ""):
            return str(value).strip()
    freshness = record.get("freshness")
    if isinstance(freshness, Mapping):
        for key in ("source_time", "updated_at", "latest_at"):
            value = freshness.get(key)
            if value not in (None, ""):
                return str(value).strip()
    return ""


def build_aeolus_bundle(
    capture: Mapping[str, Any] | None = None,
    *,
    capture_path: Path,
    profile_path: str = "",
    skill_root: Path = DEFAULT_SKILL_ROOT,
) -> SourceBundle:
    """Convert an existing Aeolus capture without fetching or writing."""

    capture = _capture_value(
        capture,
        capture_path=capture_path,
        skill_root=skill_root,
    )
    if not isinstance(capture, Mapping):
        _fail(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Aeolus capture 顶层必须是对象",
            "capture",
        )
    if capture.get("schema_version") != CAPTURE_SCHEMA:
        _fail(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            f"Aeolus capture schema_version 必须是 {CAPTURE_SCHEMA}",
            "capture.schema_version",
        )
    if (
        capture.get("capture_mode") != "snapshot"
        or capture.get("source_type") != "aeolus"
    ):
        _fail(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Aeolus adapter 只接受 capture_mode=snapshot、source_type=aeolus",
            "capture.source_type",
        )
    snapshot = capture.get("snapshot")
    if not isinstance(snapshot, Mapping):
        _fail(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Aeolus capture 缺少 snapshot 对象",
            "capture.snapshot",
        )
    if (
        snapshot.get("schema_version") != SNAPSHOT_SCHEMA
        or snapshot.get("source_type") != "aeolus"
    ):
        _fail(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Aeolus snapshot schema 或 source_type 非法",
            "capture.snapshot",
        )
    coordinates = capture.get("coordinates")
    snapshot_coordinates = snapshot.get("coordinates")
    if not isinstance(coordinates, Mapping) or snapshot_coordinates != coordinates:
        _fail(
            "SOURCE_BUNDLE_IDENTITY_MISMATCH",
            "Aeolus capture 与 snapshot 的 coordinates 不一致",
            "capture.coordinates",
        )
    expected_uid = _expected_uid(coordinates)
    if (
        capture.get("source_uid") != expected_uid
        or snapshot.get("source_uid") != expected_uid
    ):
        _fail(
            "SOURCE_BUNDLE_IDENTITY_MISMATCH",
            "Aeolus source_uid 与 coordinates 不一致",
            "capture.source_uid",
        )
    pagination = capture.get("pagination")
    if not isinstance(pagination, Mapping) or pagination.get("complete") is not True:
        _fail(
            "SOURCE_BUNDLE_INCOMPLETE",
            "Aeolus capture 必须是完整结果",
            "capture.pagination.complete",
        )
    records = snapshot.get("records")
    if not isinstance(records, list):
        _fail(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Aeolus snapshot.records 必须是数组",
            "capture.snapshot.records",
        )
    if pagination.get("item_count") not in (None, len(records)):
        _fail(
            "SOURCE_BUNDLE_INCOMPLETE",
            "Aeolus pagination.item_count 与 snapshot.records 不一致",
            "capture.pagination.item_count",
        )
    selector = snapshot.get("selector")
    if not isinstance(selector, Mapping):
        _fail(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Aeolus snapshot.selector 必须是对象",
            "capture.snapshot.selector",
        )
    report_ids = capture.get("requested_report_ids")
    where_filters = capture.get("where_filters")
    filter_mode = str(capture.get("filter_mode", "")).strip()
    if (
        not isinstance(report_ids, list)
        or not report_ids
        or not isinstance(where_filters, list)
        or any(not isinstance(item, Mapping) for item in where_filters)
        or filter_mode not in FILTER_MODES
    ):
        _fail(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Aeolus capture 的 report_ids/filter_mode/where_filters 非法",
            "capture.requested_report_ids",
        )
    if (
        selector.get("kind") != "dashboard_sheet"
        or selector.get("filter_mode") != filter_mode
        or selector.get("report_ids") != report_ids
        or selector.get("where_filters") != where_filters
    ):
        _fail(
            "SOURCE_BUNDLE_IDENTITY_MISMATCH",
            "Aeolus capture 与 snapshot.selector 不一致",
            "capture.snapshot.selector",
        )
    snapshot_hash = canonical_sha256(snapshot)
    if capture.get("content_hash") != snapshot_hash:
        _fail(
            "SOURCE_BUNDLE_HASH_MISMATCH",
            "Aeolus capture.content_hash 与 canonical snapshot hash 不一致",
            "capture.content_hash",
        )
    anchors = _anchor_index(capture.get("anchors"))
    record_index: list[dict[str, Any]] = []
    seen: set[str] = set()
    selected_report_ids: list[Any] = []
    total_rows = 0
    for index, record in enumerate(records):
        path = f"capture.snapshot.records[{index}]"
        if not isinstance(record, Mapping):
            _fail(
                "SOURCE_BUNDLE_ADAPTER_INVALID",
                f"Aeolus records[{index}] 必须是对象",
                path,
            )
        projection = project_legacy_record(record, "aeolus")
        record_id = str(projection.get("record_id", "")).strip()
        report_id = record.get("report_id")
        if (
            not record_id
            or record_id in seen
            or record_id != f"report:{report_id}"
        ):
            _fail(
                "SOURCE_BUNDLE_ADAPTER_INVALID",
                "Aeolus record_id 必须是唯一的 report:<report_id>",
                path,
            )
        seen.add(record_id)
        selected_report_ids.append(report_id)
        rows = record.get("rows")
        columns = record.get("columns")
        effective_filters = record.get("effective_filters")
        if (
            record.get("dataset_id") in (None, "")
            or not isinstance(rows, list)
            or not isinstance(columns, list)
            or not isinstance(effective_filters, list)
            or record.get("row_count") != len(rows)
        ):
            _fail(
                "SOURCE_BUNDLE_ADAPTER_INVALID",
                f"Aeolus report {report_id} 结构或 row_count 非法",
                path,
            )
        total_rows += len(rows)
        anchor_id = f"aeolus:report:{report_id}"
        anchor = anchors.get(anchor_id)
        if anchor is None:
            _fail(
                "SOURCE_BUNDLE_ADAPTER_INVALID",
                f"Aeolus report 缺少对应 anchor: {anchor_id}",
                path,
            )
        locator = _validate_record_anchor(
            anchor=anchor,
            record=record,
            coordinates=coordinates,
            path=f"capture.anchors.{anchor_id}",
        )
        title_candidates = projection.get("title_candidates", [])
        title = (
            str(title_candidates[0][1]).strip()
            if title_candidates
            else str(anchor.get("label", "")).strip() or record_id
        )
        fields = {
            str(key): value
            for key, value in record.items()
            if key not in {"record_id", "name"}
        }
        item: dict[str, Any] = {
            "record_id": record_id,
            "title": title,
            "anchor_id": anchor_id,
            "locator": dict(locator),
            "fields": fields,
        }
        source_time = _record_source_time(record)
        if source_time:
            item["source_time"] = source_time
        record_index.append(item)
    if sorted(map(str, selected_report_ids)) != sorted(map(str, report_ids)):
        _fail(
            "SOURCE_BUNDLE_IDENTITY_MISMATCH",
            "Aeolus records 与 requested_report_ids 不一致",
            "capture.requested_report_ids",
        )
    if pagination.get("row_count") not in (None, total_rows):
        _fail(
            "SOURCE_BUNDLE_INCOMPLETE",
            "Aeolus pagination.row_count 与 records 不一致",
            "capture.pagination.row_count",
        )
    record_index.sort(key=lambda item: item["record_id"])

    source_profile = capture.get("source_profile", {})
    if source_profile not in ({}, None) and not isinstance(source_profile, Mapping):
        _fail(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Aeolus source_profile 必须是对象",
            "capture.source_profile",
        )
    profile = dict(source_profile or {})
    if not profile:
        _fail(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Aeolus Bundle 必须来自已注册 Profile 的 capture",
            "capture.source_profile",
        )
    if profile.get("source_uid") != expected_uid:
        _fail(
            "SOURCE_BUNDLE_IDENTITY_MISMATCH",
            "Aeolus source_profile.source_uid 与 capture 不一致",
            "capture.source_profile.source_uid",
        )
    revision = str(profile.get("revision", "")).strip()
    if not SHA_RE.fullmatch(revision):
        _fail(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Aeolus source_profile.revision 必须是 canonical sha256",
            "capture.source_profile.revision",
        )
    if profile_path:
        profile["path"] = profile_path
    if not PROFILE_PATH_RE.fullmatch(str(profile.get("path", "")).strip()):
        _fail(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Aeolus source_profile.path 必须指向 KB sources/ 下的 Profile",
            "capture.source_profile.path",
        )

    bundle = {
        "schema_version": BUNDLE_SCHEMA,
        "identity": {
            "source_type": "aeolus",
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
                "heading": "风神看板快照",
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
            "requested_report_ids": list(report_ids),
            "filter_mode": filter_mode,
            "where_filters": list(where_filters),
            "pagination": dict(pagination),
            "sanitization": capture.get("sanitization", {}),
            "source_profile": profile,
        },
        "record_index": record_index,
        "snapshot_hash": snapshot_hash,
        "payload_hash": None,
    }
    return SourceBundle.from_dict(bundle, skill_root=skill_root)


aeolus_capture_to_bundle = build_aeolus_bundle


def aeolus_bundle_to_transaction_source(bundle: SourceBundle) -> dict[str, Any]:
    """Materialize the current digest-plan/v1 Aeolus source contract."""

    if bundle.identity.source_type != "aeolus":
        _fail(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Aeolus adapter 收到其它类型 bundle",
            "identity.source_type",
        )
    metadata = bundle.provider_metadata
    coordinates = metadata.get("coordinates")
    report_ids = metadata.get("requested_report_ids")
    where_filters = metadata.get("where_filters")
    filter_mode = str(metadata.get("filter_mode", "")).strip()
    profile = metadata.get("source_profile")
    if (
        not isinstance(coordinates, Mapping)
        or not isinstance(report_ids, list)
        or not report_ids
        or not isinstance(where_filters, list)
        or filter_mode not in FILTER_MODES
        or not isinstance(profile, Mapping)
    ):
        _fail(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Aeolus bundle provider_metadata 不完整",
            "provider_metadata",
        )
    profile_path = str(profile.get("path", "")).strip()
    profile_revision = str(profile.get("revision", "")).strip()
    if not PROFILE_PATH_RE.fullmatch(profile_path) or not SHA_RE.fullmatch(
        profile_revision
    ):
        _fail(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            "Aeolus transaction 要求合法 profile path/revision",
            "provider_metadata.source_profile",
        )
    components = []
    for component in bundle.components:
        value = component.to_dict()
        value["coverage"] = bundle.coverage.components[component.name]
        components.append(value)
    source: dict[str, Any] = {
        "type": "aeolus",
        "uid": bundle.identity.source_uid,
        "profile_path": profile_path,
        "profile_revision": profile_revision,
        "url": bundle.identity.source_url,
        "title": bundle.identity.title,
        "region": str(coordinates.get("region", "")).strip(),
        "app_id": coordinates.get("app_id"),
        "dashboard_id": coordinates.get("dashboard_id"),
        "sheet_id": coordinates.get("sheet_id"),
        "report_ids": list(report_ids),
        "filter_mode": filter_mode,
        "where_filters": list(where_filters),
        "components": components,
    }
    if bundle.record_index is not None:
        source["record_index"] = [item.to_dict() for item in bundle.record_index]
    return source


class AeolusCaptureAdapter:
    source_type = "aeolus"
    request_builder = staticmethod(build_aeolus_bundle)
    capabilities = Capabilities(
        component_kinds=frozenset({"records"}),
        coverage_dimensions=frozenset({"snapshot"}),
        stable_record_ids=True,
        record_index=True,
        incremental_diff=True,
    )

    def build_bundle(self, **kwargs: Any) -> SourceBundle:
        return build_aeolus_bundle(**kwargs)

    def validate_bundle(self, bundle: SourceBundle) -> None:
        capture_path = structured_capture_path(
            bundle,
            source_type=self.source_type,
        )
        profile = bundle.provider_metadata.get("source_profile")
        profile_path = (
            str(profile.get("path", "")).strip()
            if isinstance(profile, Mapping)
            else ""
        )
        rebuilt = build_aeolus_bundle(
            capture_path=capture_path,
            profile_path=profile_path,
        )
        require_rebuilt_bundle_match(
            bundle,
            rebuilt,
            source_type=self.source_type,
        )

    def to_transaction_source(self, bundle: SourceBundle) -> dict[str, Any]:
        return aeolus_bundle_to_transaction_source(bundle)
