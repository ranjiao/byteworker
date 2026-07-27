"""
byteworker · digest transaction core

The agent owns semantic decisions and renders complete candidate node files.
This module owns deterministic payload hashing, idempotency checks, validation,
atomic writes, derived-index rebuilds, and exact local Git commits.

Only Python's standard library is used. Business manifests and candidate files
belong in the system temporary directory or the configured KB directory, never
in the byteworker skill repository.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from constants import NODE_ID_PREFIXES, NODE_TYPES
from frontmatter import parse_file, parse_frontmatter


PLAN_SCHEMA = "digest-plan/v1"
PAYLOAD_SCHEMA = "byteworker-payload-v1"
SHA_PREFIX = "sha256:"
ALLOWED_SOURCE_TYPES = {
    "feishu_doc",
    "feishu_minutes",
    "feishu_meeting",
    "feishu_chat",
    "web",
    "local_md",
}
NODE_DIR_BY_TYPE = {node_type: dir_name for dir_name, node_type, _ in NODE_TYPES}
PROTECTED_RAW_FIELDS = {
    "raw_id",
    "ingested",
    "source_type",
    "source_uid",
    "source_revision",
    "source_url",
    "source_title",
    "source_chat_id",
    "source_chat_name",
    "digest_period",
    "source_window",
    "payload_schema",
    "payload_components",
    "body_hash",
    "comment_hash",
    "whiteboard_hash",
    "content_hash",
    "digest_key",
    "digest_status",
    "digest_targets",
}
COMPONENT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class DigestTxnError(RuntimeError):
    """A safe, user-actionable digest transaction error."""


@dataclass(frozen=True)
class ComponentPayload:
    name: str
    kind: str
    data: bytes
    digest: str
    heading: str
    mode: str
    coverage: str
    uid: str


@dataclass
class ValidationResult:
    plan: Dict[str, Any]
    source_path: Path
    payload: Dict[str, Any]
    preflight: Dict[str, Any]
    raw_id: str
    raw_path: Path
    nodes: List[Dict[str, Any]]
    node_ids: List[str]
    warnings: List[str]


def sha256_bytes(data: bytes) -> str:
    return SHA_PREFIX + hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _json_pointer(value: Any, pointer: str) -> Any:
    if not pointer:
        return value
    if pointer == "/":
        return value
    if not pointer.startswith("/"):
        raise DigestTxnError(f"json_pointer 必须以 / 开头: {pointer}")
    current = value
    for raw_part in pointer[1:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError) as exc:
                raise DigestTxnError(f"json_pointer 无法定位数组项: {pointer}") from exc
        elif isinstance(current, dict):
            if part not in current:
                raise DigestTxnError(f"json_pointer 字段不存在: {pointer}")
            current = current[part]
        else:
            raise DigestTxnError(f"json_pointer 穿过了非容器值: {pointer}")
    return current


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _resolve_input_path(raw_path: str, manifest_path: Path) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        path = manifest_path.parent / path
    path = path.resolve()
    if not path.is_file():
        raise DigestTxnError(f"输入文件不存在: {path}")
    return path


def _component_bytes(component: Dict[str, Any], manifest_path: Path) -> bytes:
    path = _resolve_input_path(str(component.get("path", "")), manifest_path)
    mode = str(component.get("mode", "verbatim"))
    pointer = str(component.get("json_pointer", ""))
    if mode == "verbatim":
        if pointer:
            value = _json_pointer(json.loads(path.read_text(encoding="utf-8")), pointer)
            if not isinstance(value, str):
                raise DigestTxnError(
                    f"verbatim + json_pointer 必须定位到字符串: {component.get('name')}"
                )
            return value.encode("utf-8")
        return path.read_bytes()
    if mode == "canonical-json":
        value = json.loads(path.read_text(encoding="utf-8"))
        value = _json_pointer(value, pointer)
        return _canonical_json_bytes(value)
    raise DigestTxnError(f"不支持的 component mode: {mode}")


def _aggregate_payload(components: Sequence[ComponentPayload]) -> str:
    digest = hashlib.sha256()
    digest.update((PAYLOAD_SCHEMA + "\0").encode("utf-8"))
    for component in sorted(components, key=lambda item: item.name):
        name = component.name.encode("utf-8")
        digest.update(struct.pack(">Q", len(name)))
        digest.update(name)
        digest.update(struct.pack(">Q", len(component.data)))
        digest.update(component.data)
    return SHA_PREFIX + digest.hexdigest()


def compute_payload(source: Dict[str, Any], manifest_path: Path) -> Dict[str, Any]:
    components_config = source.get("components")
    if not isinstance(components_config, list) or not components_config:
        raise DigestTxnError("source.components 必须是非空数组")

    seen_names = set()
    components: List[ComponentPayload] = []
    for index, raw_component in enumerate(components_config):
        if not isinstance(raw_component, dict):
            raise DigestTxnError(f"source.components[{index}] 必须是对象")
        name = str(raw_component.get("name", "")).strip()
        kind = str(raw_component.get("kind", "")).strip()
        if not name or not COMPONENT_NAME_RE.match(name):
            raise DigestTxnError(f"component name 非法: {name!r}")
        if name in seen_names:
            raise DigestTxnError(f"component name 重复: {name}")
        if not kind:
            raise DigestTxnError(f"component {name} 缺少 kind")
        seen_names.add(name)
        data = _component_bytes(raw_component, manifest_path)
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DigestTxnError(f"component {name} 不是 UTF-8 文本") from exc
        components.append(
            ComponentPayload(
                name=name,
                kind=kind,
                data=data,
                digest=sha256_bytes(data),
                heading=str(raw_component.get("heading", "")).strip(),
                mode=str(raw_component.get("mode", "verbatim")),
                coverage=str(raw_component.get("coverage", "")).strip(),
                uid=str(raw_component.get("uid", "")).strip(),
            )
        )

    body_components = [item for item in components if item.kind == "body"]
    comment_components = [item for item in components if item.kind == "comments"]
    whiteboards = [item for item in components if item.kind == "whiteboard"]
    if str(source.get("type", "")) == "feishu_doc" and len(body_components) != 1:
        raise DigestTxnError("feishu_doc 必须且只能包含一个 kind=body component")
    if str(source.get("type", "")) == "feishu_doc" and components[0].kind != "body":
        raise DigestTxnError("feishu_doc 的第一个 component 必须是 kind=body")
    if len(comment_components) > 1:
        raise DigestTxnError("一个 source 最多只能包含一个 kind=comments component")
    if str(source.get("type", "")) == "feishu_doc":
        for field in ("uid", "revision", "url", "title"):
            if not str(source.get(field, "")).strip():
                raise DigestTxnError(f"feishu_doc 的 source.{field} 不能为空")
        comments_status = str(source.get("comments_status", "")).strip()
        if comments_status not in {"complete", "partial", "unavailable"}:
            raise DigestTxnError(
                "feishu_doc 的 source.comments_status 必须是 complete/partial/unavailable"
            )
        if comments_status in {"complete", "partial"} and len(comment_components) != 1:
            raise DigestTxnError(
                f"comments_status={comments_status} 时必须包含 kind=comments component"
            )
        if comments_status == "unavailable" and comment_components:
            raise DigestTxnError(
                "comments_status=unavailable 时不得提供伪造的 comments component"
            )
        if comment_components and source.get("comment_count") not in (None, ""):
            try:
                comments_value = json.loads(comment_components[0].data.decode("utf-8"))
                actual_count = len(comments_value) if isinstance(comments_value, list) else None
                expected_count = int(source["comment_count"])
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                raise DigestTxnError("source.comment_count 或评论组件格式非法") from exc
            if actual_count is None or actual_count != expected_count:
                raise DigestTxnError(
                    f"comment_count 不一致: expected={expected_count}, actual={actual_count}"
                )
        if whiteboards:
            whiteboards_status = str(source.get("whiteboards_status", "")).strip()
            if whiteboards_status not in {"complete", "partial"}:
                raise DigestTxnError(
                    "包含 whiteboard component 时必须声明 "
                    "source.whiteboards_status=complete/partial"
                )
    source_type = str(source.get("type", ""))
    if source_type == "feishu_chat":
        for field in ("uid", "title", "source_window"):
            if not str(source.get(field, "")).strip():
                raise DigestTxnError(f"feishu_chat 的 source.{field} 不能为空")
    if source_type in {"feishu_minutes", "feishu_meeting", "web"}:
        for field in ("url", "title"):
            if not str(source.get(field, "")).strip():
                raise DigestTxnError(f"{source_type} 的 source.{field} 不能为空")

    result: Dict[str, Any] = {
        "payload_schema": PAYLOAD_SCHEMA,
        "components": components,
        "component_hashes": {item.name: item.digest for item in components},
        "content_hash": _aggregate_payload(components),
        "compatibility_hashes": {},
    }
    if body_components:
        result["body_hash"] = body_components[0].digest
        result["compatibility_hashes"]["body_hashes"] = sorted(
            {
                body_components[0].digest,
                sha256_bytes(body_components[0].data + b"\n"),
            }
        )
    if comment_components:
        result["comment_hash"] = comment_components[0].digest
        result["compatibility_hashes"]["comment_hashes"] = sorted(
            {
                comment_components[0].digest,
                sha256_bytes(comment_components[0].data + b"\n"),
            }
        )
    if whiteboards:
        result["whiteboard_hash"] = _aggregate_payload(whiteboards)
        result["whiteboard_count"] = len(whiteboards)
        legacy_whiteboards = b"".join(item.data + b"\n" for item in whiteboards)
        result["compatibility_hashes"]["whiteboard_hashes"] = sorted(
            {
                result["whiteboard_hash"],
                sha256_bytes(legacy_whiteboards),
            }
        )
    if (
        body_components
        and len(comment_components) <= 1
        and not ({item.kind for item in components} - {"body", "comments", "whiteboard"})
    ):
        legacy_payload = body_components[0].data + b"\n"
        if comment_components:
            legacy_payload += comment_components[0].data + b"\n"
        legacy_payload += b"".join(item.data + b"\n" for item in whiteboards)
        result["compatibility_hashes"]["content_hashes"] = sorted(
            {
                result["content_hash"],
                sha256_bytes(legacy_payload),
            }
        )
    return result


def _source_identity(source: Dict[str, Any]) -> Tuple[str, str, str]:
    source_type = str(source.get("type", "")).strip()
    source_uid = str(source.get("uid", "")).strip()
    period = str(source.get("digest_period") or source.get("source_window") or "").strip()
    if not source_type:
        raise DigestTxnError("source.type 不能为空")
    if source_type not in ALLOWED_SOURCE_TYPES:
        raise DigestTxnError(f"不支持的 source.type: {source_type}")
    if not source_uid:
        raise DigestTxnError("source.uid 不能为空")
    return source_type, source_uid, period


def build_digest_key(source: Dict[str, Any], payload: Dict[str, Any]) -> str:
    source_type, source_uid, period = _source_identity(source)
    return f"{source_type}:{source_uid}:{period or '-'}:{payload['content_hash']}"


def _list_value(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip().strip('"').strip("'") for item in value if str(item).strip()]
    if value:
        return [str(value).strip()]
    return []


def _raw_source_matches(fm: Dict[str, Any], source: Dict[str, Any]) -> bool:
    source_type, source_uid, period = _source_identity(source)
    if str(fm.get("source_type", "")) != source_type:
        return False
    old_uid = str(fm.get("source_uid", "")).strip()
    if old_uid:
        if old_uid != source_uid:
            return False
    else:
        source_url = str(source.get("url", "")).strip()
        if not source_url or str(fm.get("source_url", "")).strip() != source_url:
            return False
    old_period = str(fm.get("digest_period") or fm.get("source_window") or "").strip()
    return old_period == period


def _legacy_payload_matches(fm: Dict[str, Any], payload: Dict[str, Any]) -> bool:
    compatibility = payload.get("compatibility_hashes", {})
    content_hashes = set(compatibility.get("content_hashes", [payload["content_hash"]]))
    if str(fm.get("content_hash", "")) in content_hashes:
        return True
    kinds = {item.kind for item in payload["components"]}
    if kinds - {"body", "comments", "whiteboard"}:
        return False
    compatibility_fields = {
        "body_hash": set(compatibility.get("body_hashes", [])),
        "comment_hash": set(compatibility.get("comment_hashes", [])),
        "whiteboard_hash": set(compatibility.get("whiteboard_hashes", [])),
    }
    for field in compatibility_fields:
        new_value = payload.get(field)
        accepted = compatibility_fields[field] or ({new_value} if new_value else set())
        if new_value and str(fm.get(field, "")) not in accepted:
            return False
    return bool(payload.get("body_hash") or payload.get("comment_hash"))


def preflight(kb: Path, source: Dict[str, Any], manifest_path: Path) -> Dict[str, Any]:
    kb = kb.resolve()
    if not (kb / "knowledge").is_dir() or not (kb / "raw_data").is_dir():
        raise DigestTxnError(f"不是有效的 byteworker 知识库目录: {kb}")
    payload = compute_payload(source, manifest_path)
    digest_key = build_digest_key(source, payload)
    matches = []
    exact = []
    for path in sorted((kb / "raw_data").glob("*.md")):
        fm, _ = parse_file(str(path))
        if not fm or not _raw_source_matches(fm, source):
            continue
        item = {
            "path": str(path.relative_to(kb)),
            "raw_id": str(fm.get("raw_id", "")),
            "digest_status": str(fm.get("digest_status", "")),
            "digest_targets": _list_value(fm.get("digest_targets")),
            "source_revision": str(fm.get("source_revision", "")),
            "content_hash": str(fm.get("content_hash", "")),
        }
        matches.append(item)
        if str(fm.get("digest_key", "")) == digest_key or _legacy_payload_matches(fm, payload):
            exact.append(item)

    if exact:
        completed = [item for item in exact if item["digest_status"] == "digested"]
        preferred = completed[-1] if completed else exact[-1]
        state = "noop" if completed else "resume_failed"
    elif matches:
        state = "new_version"
        preferred = matches[-1]
    else:
        state = "new_source"
        preferred = None

    return {
        "state": state,
        "digest_key": digest_key,
        "payload_schema": payload["payload_schema"],
        "content_hash": payload["content_hash"],
        "component_hashes": payload["component_hashes"],
        "body_hash": payload.get("body_hash", ""),
        "comment_hash": payload.get("comment_hash", ""),
        "whiteboard_hash": payload.get("whiteboard_hash", ""),
        "whiteboard_count": payload.get("whiteboard_count", 0),
        "matched_raws": matches,
        "existing": preferred,
    }


def load_manifest(path: Path) -> Dict[str, Any]:
    path = path.resolve()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DigestTxnError(f"无法读取 manifest: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise DigestTxnError("manifest 顶层必须是对象")
    return data


def _safe_relative_path(kb: Path, value: str, allowed_prefixes: Sequence[str]) -> Path:
    raw = Path(value)
    if raw.is_absolute() or ".." in raw.parts:
        raise DigestTxnError(f"目标路径必须是知识库内相对路径: {value}")
    if not any(raw.parts and raw.parts[0] == prefix for prefix in allowed_prefixes):
        raise DigestTxnError(f"目标路径不在允许目录中: {value}")
    unresolved = kb / raw
    cursor = kb
    for part in raw.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise DigestTxnError(f"目标路径经过符号链接: {value}")
    target = unresolved.resolve()
    try:
        target.relative_to(kb.resolve())
    except ValueError as exc:
        raise DigestTxnError(f"目标路径逃逸知识库目录: {value}") from exc
    return target


def _node_path_type(kb: Path, path: Path) -> str:
    relative = path.relative_to(kb)
    if len(relative.parts) < 3 or relative.parts[0] != "knowledge":
        raise DigestTxnError(f"节点路径非法: {relative}")
    dir_name = relative.parts[1]
    for node_type, expected_dir in NODE_DIR_BY_TYPE.items():
        if dir_name == expected_dir:
            return node_type
    raise DigestTxnError(f"未知节点目录: {relative}")


def _scan_nodes(kb: Path) -> Dict[str, Dict[str, Any]]:
    nodes: Dict[str, Dict[str, Any]] = {}
    for path in sorted((kb / "knowledge").glob("**/*.md")):
        fm, _ = parse_file(str(path))
        node_id = str(fm.get("id", "")).strip()
        if not node_id:
            continue
        if node_id in nodes:
            raise DigestTxnError(f"知识库存在重复节点 id: {node_id}")
        nodes[node_id] = {
            "path": path,
            "frontmatter": fm,
            "links": set(_list_value(fm.get("links"))),
        }
    return nodes


def _validate_node_candidate(
    kb: Path,
    node: Dict[str, Any],
    manifest_path: Path,
    raw_id: str,
    existing_nodes: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    op = str(node.get("op", "")).strip()
    if op not in {"create", "update"}:
        raise DigestTxnError(f"node.op 必须是 create/update: {op}")
    target = _safe_relative_path(kb, str(node.get("path", "")), ("knowledge",))
    candidate = _resolve_input_path(str(node.get("candidate", "")), manifest_path)
    content = candidate.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(content)
    node_id = str(fm.get("id", "")).strip()
    node_type = str(fm.get("type", "")).strip()
    if not node_id or not node_id.startswith(NODE_ID_PREFIXES):
        raise DigestTxnError(f"候选节点缺少合法 id: {candidate}")
    expected_type = _node_path_type(kb, target)
    if node_type != expected_type:
        raise DigestTxnError(
            f"节点 type/path 不匹配: {node_id}: type={node_type}, path={target.relative_to(kb)}"
        )
    if not node_id.startswith(node_type + "-"):
        raise DigestTxnError(f"节点 id/type 不匹配: {node_id} vs {node_type}")
    for field in ("title", "sources", "links"):
        if field not in fm:
            raise DigestTxnError(f"候选节点 {node_id} 缺少 frontmatter.{field}")
    if raw_id not in _list_value(fm.get("sources")):
        raise DigestTxnError(f"候选节点 {node_id} 的 sources 未包含 {raw_id}")
    if not body.strip():
        raise DigestTxnError(f"候选节点正文为空: {node_id}")

    exists = target.exists()
    registered = existing_nodes.get(node_id)
    if op == "create":
        if exists or registered:
            raise DigestTxnError(f"create 节点已存在: {node_id}")
        for field in ("status", "created", "updated", "last_verified"):
            if field not in fm:
                raise DigestTxnError(f"新节点 {node_id} 缺少 frontmatter.{field}")
    else:
        if not exists or not registered:
            raise DigestTxnError(f"update 节点不存在: {node_id}")
        if registered["path"].resolve() != target:
            raise DigestTxnError(
                f"节点 id 已注册在其它路径: {node_id}: {registered['path'].relative_to(kb)}"
            )
        base_sha256 = str(node.get("base_sha256", "")).strip()
        if not base_sha256:
            raise DigestTxnError(f"update 节点必须提供 base_sha256: {node_id}")
        actual = sha256_file(target)
        if actual != base_sha256:
            raise DigestTxnError(
                f"节点基线已变化: {node_id}: expected={base_sha256}, actual={actual}"
            )

    return {
        "op": op,
        "id": node_id,
        "type": node_type,
        "path": target,
        "relative_path": str(target.relative_to(kb)),
        "candidate": candidate,
        "content": content,
        "frontmatter": fm,
        "links": set(_list_value(fm.get("links"))),
        "base_links": set(registered["links"]) if registered else set(),
    }


def _validate_scoped_links(
    existing_nodes: Dict[str, Dict[str, Any]],
    nodes: List[Dict[str, Any]],
) -> List[str]:
    warnings: List[str] = []
    final_links = {node_id: set(item["links"]) for node_id, item in existing_nodes.items()}
    final_ids = set(final_links)
    touched_ids = {node["id"] for node in nodes}
    for node in nodes:
        final_ids.add(node["id"])
        final_links[node["id"]] = set(node["links"])

    for node in nodes:
        node_id = node["id"]
        added = node["links"] - node["base_links"]
        removed = node["base_links"] - node["links"]
        unchanged = node["links"] & node["base_links"]
        if node_id in node["links"]:
            if node_id in added or node["op"] == "create":
                raise DigestTxnError(f"新增自链接: {node_id}")
            warnings.append(f"保留历史自链接: {node_id}")
        for target in sorted(added):
            if target not in final_ids:
                raise DigestTxnError(f"新增悬空链接: {node_id} -> {target}")
            if node_id not in final_links.get(target, set()):
                raise DigestTxnError(
                    f"新增链接缺少反向边: {node_id} -> {target}; 请把 {target} 纳入候选更新"
                )
        for target in sorted(removed):
            if target in final_ids and node_id in final_links.get(target, set()):
                raise DigestTxnError(
                    f"删除链接但反向边仍存在: {target} -> {node_id}; 请把 {target} 纳入候选更新"
                )
        for target in sorted(unchanged):
            if target not in final_ids:
                warnings.append(f"保留历史悬空链接: {node_id} -> {target}")
            elif node_id not in final_links.get(target, set()):
                warnings.append(f"保留历史非对称链接: {node_id} -> {target}")

    # A touched target may receive a changed reverse edge from another candidate.
    for source_id, links in final_links.items():
        for target_id in touched_ids & links:
            if source_id not in final_links.get(target_id, set()):
                source_was_touched = source_id in touched_ids
                target_base = existing_nodes.get(target_id, {}).get("links", set())
                if source_was_touched or source_id in target_base:
                    raise DigestTxnError(
                        f"事务后链接不对称: {source_id} -> {target_id}, "
                        f"但 {target_id} 未链接回 {source_id}"
                    )
                warnings.append(f"保留历史非对称链接: {source_id} -> {target_id}")
    return sorted(set(warnings))


def validate_plan(kb: Path, manifest_path: Path) -> ValidationResult:
    kb = kb.resolve()
    manifest_path = manifest_path.resolve()
    plan = load_manifest(manifest_path)
    if plan.get("schema_version") != PLAN_SCHEMA:
        raise DigestTxnError(
            f"不支持的 schema_version: {plan.get('schema_version')!r}; 需要 {PLAN_SCHEMA}"
        )
    source = plan.get("source")
    if not isinstance(source, dict):
        raise DigestTxnError("plan.source 必须是对象")
    payload = compute_payload(source, manifest_path)
    flight = preflight(kb, source, manifest_path)

    raw = plan.get("raw")
    if not isinstance(raw, dict):
        raise DigestTxnError("plan.raw 必须是对象")
    raw_id = str(raw.get("raw_id", "")).strip()
    if not raw_id.startswith("raw-"):
        raise DigestTxnError("raw.raw_id 必须以 raw- 开头")
    raw_path = _safe_relative_path(kb, str(raw.get("path", "")), ("raw_data",))
    if raw_path.suffix != ".md" or raw_path.parent != kb / "raw_data":
        raise DigestTxnError("第一版仅允许 raw_data/<name>.md")

    raw_ids = {}
    for path in (kb / "raw_data").glob("*.md"):
        fm, _ = parse_file(str(path))
        old_id = str(fm.get("raw_id", "")).strip()
        if old_id:
            raw_ids[old_id] = path
    if raw_id in raw_ids:
        raise DigestTxnError(f"raw_id 已存在: {raw_id}")
    if raw_path.exists():
        raise DigestTxnError(f"raw 目标路径已存在: {raw_path.relative_to(kb)}")

    node_configs = plan.get("nodes")
    if not isinstance(node_configs, list) or not node_configs:
        raise DigestTxnError("plan.nodes 必须是非空数组")
    existing_nodes = _scan_nodes(kb)
    nodes = [
        _validate_node_candidate(kb, item, manifest_path, raw_id, existing_nodes)
        for item in node_configs
    ]
    node_ids = [item["id"] for item in nodes]
    if len(node_ids) != len(set(node_ids)):
        raise DigestTxnError("plan.nodes 包含重复节点 id")
    paths = [item["path"] for item in nodes]
    if len(paths) != len(set(paths)):
        raise DigestTxnError("plan.nodes 包含重复目标路径")

    warnings = _validate_scoped_links(existing_nodes, nodes)
    journal = plan.get("journal")
    if not isinstance(journal, dict) or not str(journal.get("summary", "")).strip():
        raise DigestTxnError("plan.journal.summary 不能为空")
    commit = plan.get("commit")
    if not isinstance(commit, dict) or not str(commit.get("message", "")).strip():
        raise DigestTxnError("plan.commit.message 不能为空")

    if flight["state"] == "noop":
        warnings.append("相同 payload 已完成摄取; execute 将 no-op")
    result = ValidationResult(
        plan=plan,
        source_path=manifest_path,
        payload=payload,
        preflight=flight,
        raw_id=raw_id,
        raw_path=raw_path,
        nodes=nodes,
        node_ids=node_ids,
        warnings=warnings,
    )
    # Render once during validation so raw metadata errors fail before execute.
    render_raw(result, datetime(2000, 1, 1, tzinfo=ZoneInfo("Asia/Shanghai")))
    return result


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if re.fullmatch(r"[A-Za-z0-9._:/|+@=-]+", text):
        return text
    return json.dumps(text, ensure_ascii=False)


def _append_yaml_field(lines: List[str], key: str, value: Any) -> None:
    if value is None or value == "" or value == []:
        return
    if isinstance(value, list):
        lines.append(f"{key}:")
        for item in value:
            lines.append(f"  - {_yaml_scalar(item)}")
    else:
        lines.append(f"{key}: {_yaml_scalar(value)}")


def _code_fence(text: str) -> str:
    longest = max((len(match.group(0)) for match in re.finditer(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def render_raw(result: ValidationResult, now: datetime) -> str:
    plan = result.plan
    source = plan["source"]
    raw = plan["raw"]
    payload = result.payload
    digest_key = build_digest_key(source, payload)
    components: List[ComponentPayload] = payload["components"]

    lines = ["---"]
    ordered = [
        ("raw_id", result.raw_id),
        ("ingested", now.isoformat(timespec="seconds")),
        ("source_type", source.get("type")),
        ("source_uid", source.get("uid")),
        ("source_revision", source.get("revision")),
        (
            "source_chat_id",
            source.get("uid") if source.get("type") == "feishu_chat" else None,
        ),
        (
            "source_chat_name",
            source.get("title") if source.get("type") == "feishu_chat" else None,
        ),
        (
            "source_url",
            source.get("url") if source.get("type") != "feishu_chat" else None,
        ),
        (
            "source_title",
            source.get("title") if source.get("type") != "feishu_chat" else None,
        ),
        ("digest_period", source.get("digest_period")),
        ("source_window", source.get("source_window")),
        ("payload_schema", payload["payload_schema"]),
        (
            "payload_components",
            [f"{item.name}|{item.kind}|{item.digest}" for item in components],
        ),
        ("body_hash", payload.get("body_hash")),
        ("comment_hash", payload.get("comment_hash")),
        ("whiteboard_hash", payload.get("whiteboard_hash")),
        ("embedded_whiteboards", payload.get("whiteboard_count")),
        ("comments_status", source.get("comments_status")),
        ("comment_count", source.get("comment_count")),
        ("comment_reply_count", source.get("comment_reply_count")),
        ("comments_latest_at", source.get("comments_latest_at")),
        ("whiteboards_status", source.get("whiteboards_status")),
        ("content_hash", payload["content_hash"]),
        ("digest_key", digest_key),
        ("digest_status", "digested"),
        ("digest_targets", result.node_ids),
        ("excluded_dependencies", raw.get("excluded_dependencies", [])),
    ]
    for key, value in ordered:
        _append_yaml_field(lines, key, value)

    extra = raw.get("extra_frontmatter", {})
    if extra:
        if not isinstance(extra, dict):
            raise DigestTxnError("raw.extra_frontmatter 必须是对象")
        protected = PROTECTED_RAW_FIELDS & set(extra)
        if protected:
            raise DigestTxnError(
                "raw.extra_frontmatter 不得覆盖受保护字段: " + ", ".join(sorted(protected))
            )
        for key in sorted(extra):
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(key)):
                raise DigestTxnError(f"raw.extra_frontmatter 字段名非法: {key}")
            if isinstance(extra[key], dict):
                raise DigestTxnError(
                    f"raw.extra_frontmatter 第一版不支持嵌套对象: {key}"
                )
            _append_yaml_field(lines, key, extra[key])
    lines += ["---", ""]
    output = "\n".join(lines)

    body_seen = False
    for component in components:
        text = component.data.decode("utf-8")
        if component.kind == "body" and not body_seen:
            output += text
            body_seen = True
            continue
        heading = component.heading or {
            "comments": "文档评论原始快照",
            "whiteboard": f"内嵌白板原始快照 {component.uid or component.name}",
        }.get(component.kind, f"原始组件 {component.name}")
        if not output.endswith("\n"):
            output += "\n"
        output += f"\n## {heading}\n\n"
        if component.mode == "canonical-json":
            fence = _code_fence(text)
            output += f"{fence}json\n{text}"
            if not output.endswith("\n"):
                output += "\n"
            output += fence + "\n"
        else:
            output += text
            if not output.endswith("\n"):
                output += "\n"
    return output


def _git(kb: Path, args: Sequence[str], check: bool = True) -> subprocess.CompletedProcess:
    completed = subprocess.run(
        ["git", "-C", str(kb), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip()
        raise DigestTxnError(f"git {' '.join(args)} 失败: {message}")
    return completed


def _dirty_paths(kb: Path) -> set[str]:
    output = _git(kb, ["status", "--porcelain=v1", "-z"]).stdout
    chunks = [item for item in output.split("\0") if item]
    paths = set()
    index = 0
    while index < len(chunks):
        item = chunks[index]
        status = item[:2]
        value = item[3:]
        if status.startswith("R") or status.startswith("C"):
            index += 1
            if index < len(chunks):
                value = chunks[index]
        paths.add(value)
        index += 1
    return paths


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def _journal_path(kb: Path, now: datetime) -> Path:
    return kb / "journal" / now.strftime("%Y-%m") / (now.strftime("%Y-%m-%d") + ".md")


def _journal_content(
    existing: Optional[bytes],
    result: ValidationResult,
    now: datetime,
) -> bytes:
    text = existing.decode("utf-8") if existing is not None else f"# {now:%Y-%m-%d}\n"
    if text and not text.endswith("\n"):
        text += "\n"
    summary = str(result.plan["journal"]["summary"]).strip().replace("\n", " ")
    touched = ", ".join(result.node_ids)
    text += (
        f"- {now:%H:%M} digest {summary} | nodes={touched} | "
        f"raw_id={result.raw_id} | conflict=no\n"
    )
    return text.encode("utf-8")


def _restore_files(snapshots: Dict[Path, Optional[bytes]]) -> None:
    for path, content in snapshots.items():
        if content is None:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        else:
            _atomic_write(path, content)


def _rebuild_index(skill_root: Path, kb: Path) -> None:
    script = skill_root / "bin" / "rebuild_index.py"
    completed = subprocess.run(
        [sys.executable, str(script), str(kb)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        raise DigestTxnError(
            "INDEX 重建失败: " + (completed.stderr.strip() or completed.stdout.strip())
        )


def _validate_written_nodes(result: ValidationResult) -> None:
    for node in result.nodes:
        if sha256_bytes(node["path"].read_bytes()) != sha256_bytes(node["content"].encode("utf-8")):
            raise DigestTxnError(f"节点写入后 hash 不一致: {node['id']}")
        fm, _ = parse_file(str(node["path"]))
        if str(fm.get("id", "")) != node["id"]:
            raise DigestTxnError(f"节点写入后 id 不一致: {node['id']}")


def execute_plan(
    kb: Path,
    manifest_path: Path,
    skill_root: Path,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    kb = kb.resolve()
    skill_root = skill_root.resolve()
    if not (kb / ".git").is_dir():
        raise DigestTxnError("知识库数据目录不是本地 Git 仓库，无法创建 digest 回滚点")
    manifest = load_manifest(manifest_path)
    source = manifest.get("source")
    if not isinstance(source, dict):
        raise DigestTxnError("plan.source 必须是对象")
    early_flight = preflight(kb, source, manifest_path.resolve())
    if early_flight["state"] == "noop":
        existing = early_flight.get("existing") or {}
        return {
            "status": "noop",
            "raw_id": existing.get("raw_id", ""),
            "digest_targets": existing.get("digest_targets", []),
            "digest_key": early_flight["digest_key"],
            "warnings": ["相同 payload 已完成摄取"],
        }
    if early_flight["state"] == "resume_failed":
        existing = early_flight.get("existing") or {}
        raise DigestTxnError(
            "发现相同 payload 的 pending/failed raw，第一版不自动覆盖或续写；"
            f"请先检查 {existing.get('path', existing.get('raw_id', '历史 raw'))}"
        )

    result = validate_plan(kb, manifest_path)
    if result.preflight["state"] == "noop":
        existing = result.preflight.get("existing") or {}
        return {
            "status": "noop",
            "raw_id": existing.get("raw_id", ""),
            "digest_targets": existing.get("digest_targets", []),
            "digest_key": result.preflight["digest_key"],
            "warnings": result.warnings,
        }

    if now is None:
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
    journal_path = _journal_path(kb, now)
    index_path = kb / "INDEX.md"
    target_paths = [result.raw_path, journal_path, index_path] + [
        node["path"] for node in result.nodes
    ]
    relative_paths = [str(path.relative_to(kb)) for path in target_paths]

    lock_path = kb / ".git" / "byteworker-digest.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)

        remotes = _git(kb, ["remote"]).stdout.splitlines()
        if remotes:
            raise DigestTxnError(
                "知识库 Git 配置了 remote，违反本地机密库约束: " + ", ".join(remotes)
            )
        staged = _git(kb, ["diff", "--cached", "--name-only"]).stdout.splitlines()
        if staged:
            raise DigestTxnError(
                "知识库已有暂存变更，拒绝混入 digest commit: " + ", ".join(staged)
            )
        dirty = _dirty_paths(kb)
        overlap = sorted(dirty & set(relative_paths))
        if overlap:
            raise DigestTxnError(
                "本次目标文件已有未提交变更，需先处理后再执行: " + ", ".join(overlap)
            )

        # Re-validate baselines after acquiring the lock.
        result = validate_plan(kb, manifest_path)
        snapshots = {
            path: path.read_bytes() if path.exists() else None for path in target_paths
        }
        git_index_path = kb / ".git" / "index"
        git_index_snapshot = (
            git_index_path.read_bytes() if git_index_path.exists() else None
        )
        staged_by_txn = False
        try:
            _atomic_write(result.raw_path, render_raw(result, now).encode("utf-8"))
            for node in result.nodes:
                _atomic_write(node["path"], node["content"].encode("utf-8"))
            _atomic_write(
                journal_path,
                _journal_content(snapshots[journal_path], result, now),
            )
            _rebuild_index(skill_root, kb)
            _validate_written_nodes(result)

            raw_fm, _ = parse_file(str(result.raw_path))
            if str(raw_fm.get("content_hash", "")) != result.payload["content_hash"]:
                raise DigestTxnError("raw 写入后的 content_hash 不一致")
            if set(_list_value(raw_fm.get("digest_targets"))) != set(result.node_ids):
                raise DigestTxnError("raw 写入后的 digest_targets 不一致")

            diff_check = _git(
                kb,
                ["diff", "--check", "--", *relative_paths],
                check=False,
            )
            if diff_check.returncode != 0:
                raise DigestTxnError(
                    "git diff --check 失败: "
                    + (diff_check.stdout.strip() or diff_check.stderr.strip())
                )

            _git(kb, ["add", "--", *relative_paths])
            staged_by_txn = True
            staged_after = set(
                _git(kb, ["diff", "--cached", "--name-only"]).stdout.splitlines()
            )
            unexpected = staged_after - set(relative_paths)
            if unexpected:
                raise DigestTxnError(
                    "暂存区出现事务外文件: " + ", ".join(sorted(unexpected))
                )
            if not staged_after:
                raise DigestTxnError("事务没有产生可提交变更")
            commit_message = str(result.plan["commit"]["message"]).strip()
            _git(kb, ["commit", "-m", commit_message])
            commit_hash = _git(kb, ["rev-parse", "HEAD"]).stdout.strip()
        except Exception:
            if staged_by_txn:
                if git_index_snapshot is None:
                    try:
                        git_index_path.unlink()
                    except FileNotFoundError:
                        pass
                else:
                    _atomic_write(git_index_path, git_index_snapshot)
            _restore_files(snapshots)
            raise
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    created = [node["id"] for node in result.nodes if node["op"] == "create"]
    updated = [node["id"] for node in result.nodes if node["op"] == "update"]
    return {
        "status": "committed",
        "source_state": result.preflight["state"],
        "raw_id": result.raw_id,
        "raw_path": str(result.raw_path.relative_to(kb)),
        "digest_key": result.preflight["digest_key"],
        "content_hash": result.payload["content_hash"],
        "created": created,
        "updated": updated,
        "index_rebuilt": True,
        "journal": str(journal_path.relative_to(kb)),
        "commit": commit_hash,
        "warnings": result.warnings,
    }


def validation_report(result: ValidationResult) -> Dict[str, Any]:
    return {
        "status": "valid",
        "source_state": result.preflight["state"],
        "raw_id": result.raw_id,
        "raw_path": str(result.raw_path),
        "digest_key": result.preflight["digest_key"],
        "content_hash": result.payload["content_hash"],
        "component_hashes": result.payload["component_hashes"],
        "nodes": [
            {
                "op": node["op"],
                "id": node["id"],
                "path": node["relative_path"],
            }
            for node in result.nodes
        ],
        "warnings": result.warnings,
    }
