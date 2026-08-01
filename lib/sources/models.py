"""Strict, provider-neutral source bundle model.

The bundle is the executable hand-off between provider-specific capture and
the semantic/transaction pipeline.  It deliberately does not define a common
content AST: provider payloads remain in typed external components.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from credential_safety import credential_url_fields, is_credential_field

BUNDLE_SCHEMA = "byteworker-source-bundle/v2"
RECORD_INDEX_SCHEMA = "byteworker-record-index/v1"
SHA_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
SOURCE_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*$")

COMPONENT_MODES = {"verbatim", "canonical-json"}
COVERAGE_STATES = {"complete", "partial", "unavailable", "not_applicable"}
PRECISIONS = {"exact", "refetched", "source_only", "unresolved"}
ANCHOR_KINDS = {
    "source",
    "doc_block",
    "doc_comment",
    "doc_reply",
    "chat_message",
    "chat_thread",
    "minutes_segment",
    "meeting",
    "meego_workitem",
    "base_record",
    "aeolus_report",
    "web_section",
    "whiteboard_node",
    "local_span",
}
SAFE_URL_SCHEMES = {"http", "https"}
DEFAULT_SKILL_ROOT = Path(__file__).resolve().parents[2]


class SourceBundleError(ValueError):
    """Stable validation failure at the source/digest boundary."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        path: str = "",
        hint: str = "",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.path = path
        self.hint = hint

    def as_dict(self) -> dict[str, str]:
        result = {"code": self.code, "message": str(self)}
        if self.path:
            result["path"] = self.path
        if self.hint:
            result["hint"] = self.hint
        return result


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def ensure_source_request_safe(value: Any) -> None:
    """Reject credential-shaped values before provider request dispatch."""

    _reject_credentials(value)


def _error(message: str, path: str, code: str = "SOURCE_BUNDLE_INVALID") -> None:
    raise SourceBundleError(code, message, path=path)


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _error(f"{path} 必须是对象", path)
    return value


def _sequence(value: Any, path: str) -> Sequence[Any]:
    if not isinstance(value, (list, tuple)):
        _error(f"{path} 必须是数组", path)
    return value


def _reject_unknown(
    value: Mapping[str, Any],
    allowed: set[str],
    path: str,
) -> None:
    unknown = sorted(str(key) for key in set(value) - allowed)
    if unknown:
        _error(f"{path} 含未知字段: {', '.join(unknown)}", path)


def _required_text(value: Any, path: str) -> str:
    text = str(value or "").strip()
    if not text:
        _error(f"{path} 不得为空", path)
    return text


def _optional_text(value: Any) -> str:
    return str(value or "").strip()


def _validate_hash(value: Any, path: str, *, optional: bool) -> str | None:
    if value in (None, ""):
        if optional:
            return None
        _error(f"{path} 不得为空", path)
    normalized = str(value).strip()
    if not SHA_RE.fullmatch(normalized):
        _error(f"{path} 必须是 sha256: 加 64 位小写十六进制", path)
    return normalized


def _url_has_credentials(value: str) -> bool:
    return bool(credential_url_fields(value))


def _validate_url(value: str, path: str, *, allow_empty: bool = True) -> str:
    if not value and allow_empty:
        return ""
    parsed = urlsplit(value)
    if parsed.scheme not in SAFE_URL_SCHEMES or not parsed.netloc:
        _error(f"{path} 必须是安全的 HTTP(S) URL", path)
    if _url_has_credentials(value):
        _error(
            f"{path} 不得包含凭据参数",
            path,
            "SOURCE_BUNDLE_CONTAINS_CREDENTIAL",
        )
    return value


def _reject_credentials(value: Any, path: str = "bundle") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if is_credential_field(key):
                _error(
                    f"source bundle 不得保存凭据字段: {path}.{key}",
                    f"{path}.{key}",
                    "SOURCE_BUNDLE_CONTAINS_CREDENTIAL",
                )
            _reject_credentials(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _reject_credentials(child, f"{path}[{index}]")
    elif isinstance(value, str):
        for match in re.findall(r"https?://[^\s<>\"']+", value):
            if _url_has_credentials(match):
                _error(
                    f"source bundle 不得保存含凭据参数的 URL: {path}",
                    path,
                    "SOURCE_BUNDLE_CONTAINS_CREDENTIAL",
                )


def _json_value(value: Any, path: str) -> Any:
    try:
        serialized = json.dumps(value, ensure_ascii=False, allow_nan=False)
        return json.loads(serialized)
    except (TypeError, ValueError) as exc:
        raise SourceBundleError(
            "SOURCE_BUNDLE_INVALID",
            f"{path} 必须是可序列化的 JSON 值",
            path=path,
        ) from exc


def _validate_component_path(
    value: Any,
    path: str,
    *,
    skill_root: Path,
) -> str:
    raw = _required_text(value, path)
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        _error(f"{path} 必须是绝对路径", path)
    resolved = candidate.resolve(strict=False)
    root = skill_root.expanduser().resolve(strict=False)
    if resolved == root or root in resolved.parents:
        _error(
            "业务 component 不得位于 byteworker skill 仓库",
            path,
            "SOURCE_BUNDLE_PATH_IN_SKILL_REPO",
        )
    return str(resolved)


@dataclass(frozen=True)
class SourceIdentity:
    source_type: str
    source_uid: str
    source_url: str
    title: str
    revision: str | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SourceIdentity":
        value = _mapping(value, "identity")
        _reject_unknown(
            value,
            {"source_type", "source_uid", "source_url", "title", "revision"},
            "identity",
        )
        source_type = _required_text(value.get("source_type"), "identity.source_type")
        if not SOURCE_TYPE_RE.fullmatch(source_type):
            _error("identity.source_type 格式非法", "identity.source_type")
        source_uid = _required_text(value.get("source_uid"), "identity.source_uid")
        source_url = _validate_url(
            _optional_text(value.get("source_url")),
            "identity.source_url",
        )
        title = _required_text(value.get("title"), "identity.title")
        revision = _optional_text(value.get("revision")) or None
        return cls(source_type, source_uid, source_url, title, revision)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "source_type": self.source_type,
            "source_uid": self.source_uid,
            "source_url": self.source_url,
            "title": self.title,
        }
        if self.revision is not None:
            result["revision"] = self.revision
        return result


SourceRef = SourceIdentity


@dataclass(frozen=True)
class SourceCapabilities:
    """What an adapter can truthfully guarantee at the bundle boundary."""

    component_kinds: frozenset[str]
    coverage_dimensions: frozenset[str]
    stable_record_ids: bool = False
    record_index: bool = False
    incremental_diff: bool = False

    def __post_init__(self) -> None:
        if not self.component_kinds:
            raise ValueError("component_kinds 不得为空")
        if not self.coverage_dimensions:
            raise ValueError("coverage_dimensions 不得为空")
        if self.record_index and not self.stable_record_ids:
            raise ValueError("record_index 要求 stable_record_ids")
        if self.incremental_diff and not self.stable_record_ids:
            raise ValueError("incremental_diff 要求 stable_record_ids")


@dataclass(frozen=True)
class SourceComponent:
    name: str
    kind: str
    path: str
    mode: str
    json_pointer: str | None = None
    heading: str | None = None
    uid: str | None = None
    media_type: str | None = None

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
        *,
        skill_root: Path = DEFAULT_SKILL_ROOT,
        path_prefix: str = "components[]",
    ) -> "SourceComponent":
        value = _mapping(value, path_prefix)
        _reject_unknown(
            value,
            {
                "name",
                "kind",
                "path",
                "mode",
                "json_pointer",
                "heading",
                "uid",
                "media_type",
            },
            path_prefix,
        )
        name = _required_text(value.get("name"), f"{path_prefix}.name")
        kind = _required_text(value.get("kind"), f"{path_prefix}.kind")
        if not IDENTIFIER_RE.fullmatch(name) or not IDENTIFIER_RE.fullmatch(kind):
            _error(
                f"{path_prefix}.name/kind 格式非法",
                path_prefix,
            )
        component_path = _validate_component_path(
            value.get("path"),
            f"{path_prefix}.path",
            skill_root=skill_root,
        )
        mode = _required_text(value.get("mode"), f"{path_prefix}.mode")
        if mode not in COMPONENT_MODES:
            _error(
                f"{path_prefix}.mode 必须是 {', '.join(sorted(COMPONENT_MODES))}",
                f"{path_prefix}.mode",
            )
        json_pointer = _optional_text(value.get("json_pointer")) or None
        if json_pointer is not None and not json_pointer.startswith("/"):
            _error(
                f"{path_prefix}.json_pointer 必须以 / 开头",
                f"{path_prefix}.json_pointer",
            )
        return cls(
            name=name,
            kind=kind,
            path=component_path,
            mode=mode,
            json_pointer=json_pointer,
            heading=_optional_text(value.get("heading")) or None,
            uid=_optional_text(value.get("uid")) or None,
            media_type=_optional_text(value.get("media_type")) or None,
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": self.name,
            "kind": self.kind,
            "path": self.path,
            "mode": self.mode,
        }
        for key, value in (
            ("json_pointer", self.json_pointer),
            ("heading", self.heading),
            ("uid", self.uid),
            ("media_type", self.media_type),
        ):
            if value is not None:
                result[key] = value
        return result


def _derived_coverage_status(states: Sequence[str]) -> str:
    material = [state for state in states if state != "not_applicable"]
    if not material:
        return "complete"
    if all(state == "complete" for state in material):
        return "complete"
    if all(state == "unavailable" for state in material):
        return "unavailable"
    return "partial"


@dataclass(frozen=True)
class Coverage:
    status: str
    components: Mapping[str, str]
    notes: tuple[str, ...] = ()

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
        *,
        component_names: Sequence[str],
    ) -> "Coverage":
        value = _mapping(value, "coverage")
        _reject_unknown(value, {"status", "components", "notes"}, "coverage")
        status = _required_text(value.get("status"), "coverage.status")
        if status not in COVERAGE_STATES - {"not_applicable"}:
            _error(
                "coverage.status 必须是 complete/partial/unavailable",
                "coverage.status",
            )
        raw_components = _mapping(value.get("components"), "coverage.components")
        components: dict[str, str] = {}
        for raw_name, raw_state in raw_components.items():
            name = _required_text(raw_name, "coverage.components key")
            if not IDENTIFIER_RE.fullmatch(name):
                _error("coverage component 名称格式非法", f"coverage.components.{name}")
            state = _required_text(raw_state, f"coverage.components.{name}")
            if state not in COVERAGE_STATES:
                _error(
                    "coverage component 状态非法",
                    f"coverage.components.{name}",
                )
            components[name] = state
        missing = sorted(set(component_names) - set(components))
        if missing:
            _error(
                "coverage 缺少已提供 component: " + ", ".join(missing),
                "coverage.components",
            )
        unsupported_complete = sorted(
            name
            for name, state in components.items()
            if name not in component_names and state in {"complete", "partial"}
        )
        if unsupported_complete:
            _error(
                "coverage 不得宣称未提供的 component 已读取: "
                + ", ".join(unsupported_complete),
                "coverage.components",
            )
        derived = _derived_coverage_status(tuple(components.values()))
        if status != derived:
            _error(
                f"coverage.status={status} 与各 component 推导结果 {derived} 不一致",
                "coverage.status",
            )
        raw_notes = value.get("notes", [])
        notes = tuple(
            _required_text(note, f"coverage.notes[{index}]")
            for index, note in enumerate(_sequence(raw_notes, "coverage.notes"))
        )
        return cls(status=status, components=components, notes=notes)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": self.status,
            "components": dict(self.components),
        }
        if self.notes:
            result["notes"] = list(self.notes)
        return result


def _validate_anchor(
    value: Mapping[str, Any],
    *,
    index: int,
    component_names: set[str],
) -> dict[str, Any]:
    path = f"anchors[{index}]"
    value = _mapping(value, path)
    _reject_unknown(
        value,
        {
            "anchor_id",
            "kind",
            "precision",
            "locator",
            "open_url",
            "fallback_url",
            "label",
            "source_time",
            "author",
            "quote",
            "component",
        },
        path,
    )
    anchor_id = _required_text(value.get("anchor_id"), f"{path}.anchor_id")
    if not IDENTIFIER_RE.fullmatch(anchor_id):
        _error(f"{path}.anchor_id 格式非法", f"{path}.anchor_id")
    kind = _required_text(value.get("kind"), f"{path}.kind")
    if kind not in ANCHOR_KINDS:
        _error(f"{path}.kind 非法: {kind}", f"{path}.kind")
    precision = _required_text(value.get("precision"), f"{path}.precision")
    if precision not in PRECISIONS:
        _error(f"{path}.precision 非法: {precision}", f"{path}.precision")
    locator = _mapping(value.get("locator", {}), f"{path}.locator")
    locator = _json_value(locator, f"{path}.locator")
    if precision in {"exact", "refetched"} and not locator:
        _error(f"{path} 精确定位必须包含 locator", f"{path}.locator")
    open_url = _validate_url(
        _optional_text(value.get("open_url")),
        f"{path}.open_url",
    )
    fallback_url = _validate_url(
        _optional_text(value.get("fallback_url")),
        f"{path}.fallback_url",
    )
    if not open_url and not fallback_url and not locator:
        _error(f"{path} 没有 URL 或 locator", path)
    component = _optional_text(value.get("component"))
    if component and component not in component_names:
        _error(
            f"{path}.component 指向不存在的 component: {component}",
            f"{path}.component",
        )
    result: dict[str, Any] = {
        "anchor_id": anchor_id,
        "kind": kind,
        "precision": precision,
        "locator": locator,
    }
    for key, child in (
        ("open_url", open_url),
        ("fallback_url", fallback_url),
        ("label", _optional_text(value.get("label"))),
        ("source_time", _optional_text(value.get("source_time"))),
        ("author", _optional_text(value.get("author"))),
        ("quote", _optional_text(value.get("quote"))),
        ("component", component),
    ):
        if child:
            result[key] = child
    return result


@dataclass(frozen=True)
class RecordIndexEntry:
    record_id: str
    title: str
    locator: Mapping[str, Any]
    fields: Mapping[str, Any]
    anchor_id: str | None = None
    source_time: str | None = None

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
        *,
        index: int,
        anchor_ids: set[str],
    ) -> "RecordIndexEntry":
        path = f"record_index[{index}]"
        value = _mapping(value, path)
        _reject_unknown(
            value,
            {
                "record_id",
                "title",
                "locator",
                "fields",
                "anchor_id",
                "source_time",
            },
            path,
        )
        record_id = _required_text(value.get("record_id"), f"{path}.record_id")
        title = _required_text(value.get("title"), f"{path}.title")
        locator = _json_value(
            _mapping(value.get("locator"), f"{path}.locator"),
            f"{path}.locator",
        )
        if not locator:
            _error(f"{path}.locator 不得为空", f"{path}.locator")
        fields = _json_value(
            _mapping(value.get("fields"), f"{path}.fields"),
            f"{path}.fields",
        )
        anchor_id = _optional_text(value.get("anchor_id")) or None
        if anchor_id is not None and anchor_id not in anchor_ids:
            _error(
                f"{path}.anchor_id 指向不存在的 anchor: {anchor_id}",
                f"{path}.anchor_id",
            )
        return cls(
            record_id=record_id,
            title=title,
            locator=locator,
            fields=fields,
            anchor_id=anchor_id,
            source_time=_optional_text(value.get("source_time")) or None,
        )

    def to_dict(self) -> dict[str, Any]:
        result = {
            "record_id": self.record_id,
            "title": self.title,
            "locator": copy.deepcopy(dict(self.locator)),
            "fields": copy.deepcopy(dict(self.fields)),
        }
        if self.anchor_id is not None:
            result["anchor_id"] = self.anchor_id
        if self.source_time is not None:
            result["source_time"] = self.source_time
        return result


@dataclass(frozen=True)
class SourceBundle:
    identity: SourceIdentity
    components: tuple[SourceComponent, ...]
    coverage: Coverage
    anchors: tuple[Mapping[str, Any], ...]
    provider_metadata: Mapping[str, Any]
    record_index: tuple[RecordIndexEntry, ...] | None = None
    snapshot_hash: str | None = None
    payload_hash: str | None = None
    schema_version: str = BUNDLE_SCHEMA

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
        *,
        skill_root: Path = DEFAULT_SKILL_ROOT,
    ) -> "SourceBundle":
        value = _mapping(value, "bundle")
        _reject_credentials(value)
        _reject_unknown(
            value,
            {
                "schema_version",
                "identity",
                "components",
                "coverage",
                "anchors",
                "provider_metadata",
                "record_index",
                "snapshot_hash",
                "payload_hash",
            },
            "bundle",
        )
        if value.get("schema_version") != BUNDLE_SCHEMA:
            _error(
                f"schema_version 必须是 {BUNDLE_SCHEMA}",
                "schema_version",
            )
        identity = SourceIdentity.from_dict(_mapping(value.get("identity"), "identity"))
        components = tuple(
            SourceComponent.from_dict(
                component,
                skill_root=skill_root,
                path_prefix=f"components[{index}]",
            )
            for index, component in enumerate(
                _sequence(value.get("components"), "components")
            )
        )
        if not components:
            _error("components 至少包含一个 component", "components")
        component_names = [component.name for component in components]
        if len(component_names) != len(set(component_names)):
            _error("components.name 必须唯一", "components")
        coverage = Coverage.from_dict(
            _mapping(value.get("coverage"), "coverage"),
            component_names=component_names,
        )
        anchors = tuple(
            _validate_anchor(
                anchor,
                index=index,
                component_names=set(component_names),
            )
            for index, anchor in enumerate(
                _sequence(value.get("anchors"), "anchors")
            )
        )
        anchor_ids = [str(anchor["anchor_id"]) for anchor in anchors]
        if len(anchor_ids) != len(set(anchor_ids)):
            _error("anchors.anchor_id 必须唯一", "anchors")
        provider_metadata = _json_value(
            _mapping(value.get("provider_metadata"), "provider_metadata"),
            "provider_metadata",
        )
        raw_index = value.get("record_index")
        record_index: tuple[RecordIndexEntry, ...] | None = None
        if raw_index is not None:
            record_index = tuple(
                RecordIndexEntry.from_dict(
                    item,
                    index=index,
                    anchor_ids=set(anchor_ids),
                )
                for index, item in enumerate(
                    _sequence(raw_index, "record_index")
                )
            )
            record_ids = [item.record_id for item in record_index]
            if len(record_ids) != len(set(record_ids)):
                _error("record_index.record_id 必须唯一", "record_index")
        snapshot_hash = _validate_hash(
            value.get("snapshot_hash"),
            "snapshot_hash",
            optional=True,
        )
        payload_hash = _validate_hash(
            value.get("payload_hash"),
            "payload_hash",
            optional=True,
        )
        return cls(
            identity=identity,
            components=components,
            coverage=coverage,
            anchors=anchors,
            provider_metadata=provider_metadata,
            record_index=record_index,
            snapshot_hash=snapshot_hash,
            payload_hash=payload_hash,
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "identity": self.identity.to_dict(),
            "components": [component.to_dict() for component in self.components],
            "coverage": self.coverage.to_dict(),
            "anchors": copy.deepcopy(list(self.anchors)),
            "provider_metadata": copy.deepcopy(dict(self.provider_metadata)),
            "snapshot_hash": self.snapshot_hash,
            "payload_hash": self.payload_hash,
        }
        if self.record_index is not None:
            result["record_index"] = [
                item.to_dict() for item in self.record_index
            ]
        return result


def validate_source_bundle(
    value: Mapping[str, Any],
    *,
    skill_root: Path = DEFAULT_SKILL_ROOT,
) -> dict[str, Any]:
    """Validate and return a normalized JSON-compatible bundle."""

    return SourceBundle.from_dict(value, skill_root=skill_root).to_dict()


def load_source_bundle(
    path: Path,
    *,
    skill_root: Path = DEFAULT_SKILL_ROOT,
) -> SourceBundle:
    """Load a JSON bundle outside the skill repository and validate it."""

    resolved = path.expanduser().resolve(strict=False)
    root = skill_root.expanduser().resolve(strict=False)
    if resolved == root or root in resolved.parents:
        raise SourceBundleError(
            "SOURCE_BUNDLE_PATH_IN_SKILL_REPO",
            "业务 source bundle 不得位于 byteworker skill 仓库",
            path=str(resolved),
        )
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceBundleError(
            "SOURCE_BUNDLE_READ_FAILED",
            f"无法读取 source bundle: {resolved}",
            path=str(resolved),
        ) from exc
    return SourceBundle.from_dict(value, skill_root=skill_root)
