"""Deterministic preprocessing for digest semantic work.

The module removes provider JSON noise and builds one private, reusable packet.
It discovers candidates and locators only; semantic decisions remain with the
agent and the policies in ``references/``.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Mapping

from snapshot_store import diff_current_against_kb
from source_capture import SourceCaptureError, meego_record_id
from sources import SourceBundleError, canonical_sha256, load_source_bundle


ANALYSIS_PACKET_SCHEMA = "byteworker-digest-analysis-packet/v1"
ANALYSIS_ALGORITHM_VERSION = 2
DEFAULT_SKILL_ROOT = Path(__file__).resolve().parents[1]
MAX_COMPONENT_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_CONTEXT_CHARS = 320

OPEN_ID_RE = re.compile(r"\bou_[0-9a-f]{16,}\b")
URL_RE = re.compile(r"https?://[^\s<>\"']+")
DOC_ID_RE = re.compile(r"\b(?:doc-id|document_id)=[\"']([^\"']+)[\"']", re.I)
HTML_HEADING_RE = re.compile(r"<h[1-6][^>]*>(.*?)</h[1-6]>", re.I | re.S)
MARKDOWN_HEADING_RE = re.compile(r"(?m)^#{1,6}\s+(.+?)\s*$")
DEPENDENCY_HINT_RE = re.compile(
    r"详见|参见|参考|基于|沿用|取代|变更|依赖|数据见|决议见|"
    r"会议|方案|文档|附件|see also|depends? on|supersed",
    re.I,
)
TEXT_KEYS = {
    "content",
    "description",
    "label",
    "name",
    "quote",
    "summary",
    "text",
    "title",
}


class DigestAnalysisError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        hint: str = "",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint
        self.details = dict(details or {})

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code, "message": str(self)}
        if self.hint:
            result["hint"] = self.hint
        if self.details:
            result["details"] = self.details
        return result


class _TextExtractor(HTMLParser):
    BLOCK_TAGS = {
        "br",
        "div",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "p",
        "table",
        "tr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _outside_skill(path: Path, *, skill_root: Path = DEFAULT_SKILL_ROOT) -> Path:
    resolved = path.expanduser().resolve(strict=False)
    root = skill_root.expanduser().resolve(strict=False)
    if resolved == root or root in resolved.parents:
        raise DigestAnalysisError(
            "DIGEST_ANALYSIS_PATH_IN_SKILL_REPO",
            "digest analysis 业务产物必须位于系统临时目录或知识库，不能写进 skill 仓库。",
            details={"path": str(resolved)},
        )
    return resolved


def _json_pointer(value: Any, pointer: str) -> Any:
    if not pointer:
        return value
    if not pointer.startswith("/"):
        raise DigestAnalysisError(
            "DIGEST_ANALYSIS_COMPONENT_INVALID",
            f"json_pointer 必须以 / 开头: {pointer}",
        )
    current = value
    for raw_part in pointer.split("/")[1:]:
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError) as exc:
                raise DigestAnalysisError(
                    "DIGEST_ANALYSIS_COMPONENT_INVALID",
                    f"json_pointer 无法定位数组项: {pointer}",
                ) from exc
        elif isinstance(current, Mapping):
            if part not in current:
                raise DigestAnalysisError(
                    "DIGEST_ANALYSIS_COMPONENT_INVALID",
                    f"json_pointer 字段不存在: {pointer}",
                )
            current = current[part]
        else:
            raise DigestAnalysisError(
                "DIGEST_ANALYSIS_COMPONENT_INVALID",
                f"json_pointer 穿过了非容器值: {pointer}",
            )
    return current


def _read_component_bytes(component: Any) -> tuple[bytes, int, str]:
    path = Path(component.path)
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise DigestAnalysisError(
            "DIGEST_ANALYSIS_COMPONENT_READ_FAILED",
            f"无法读取 component: {component.name}",
            details={"path": str(path)},
        ) from exc
    if size > MAX_COMPONENT_BYTES:
        raise DigestAnalysisError(
            "DIGEST_ANALYSIS_COMPONENT_TOO_LARGE",
            f"component 超过 {MAX_COMPONENT_BYTES} bytes: {component.name}",
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise DigestAnalysisError(
            "DIGEST_ANALYSIS_COMPONENT_READ_FAILED",
            f"无法读取 component: {component.name}",
            details={"path": str(path)},
        ) from exc
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    return raw, size, digest


def _decode_component(component: Any, raw: bytes) -> Any:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DigestAnalysisError(
            "DIGEST_ANALYSIS_COMPONENT_READ_FAILED",
            f"component 必须是 UTF-8 文本: {component.name}",
            details={"path": str(component.path)},
        ) from exc
    if component.mode == "verbatim" and not component.json_pointer:
        return text
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DigestAnalysisError(
            "DIGEST_ANALYSIS_COMPONENT_INVALID",
            f"component 不是合法 JSON: {component.name}",
        ) from exc
    value = _json_pointer(value, component.json_pointer or "")
    if component.mode == "verbatim" and not isinstance(value, str):
        raise DigestAnalysisError(
            "DIGEST_ANALYSIS_COMPONENT_INVALID",
            f"verbatim component 的 json_pointer 必须定位字符串: {component.name}",
        )
    return value


def _plain_text(value: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(value)
        parser.close()
        text = "".join(parser.parts)
    except ValueError:
        text = html.unescape(value)
    lines = []
    for line in text.splitlines():
        normalized = re.sub(r"[ \t\r\f\v]+", " ", line).strip()
        if normalized:
            lines.append(normalized)
    return "\n".join(lines)


def _walk_text(value: Any, path: str = "$") -> Iterable[dict[str, str]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}/{str(key).replace('~', '~0').replace('/', '~1')}"
            if str(key).lower() in TEXT_KEYS and isinstance(child, str):
                text = _plain_text(child)
                if text:
                    yield {"path": child_path, "text": text}
            else:
                yield from _walk_text(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_text(child, f"{path}/{index}")


def _dedupe_items(items: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    result = []
    for item in items:
        key = (item["path"], item["text"])
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _context(value: str, start: int, end: int) -> str:
    left = max(0, start - MAX_CONTEXT_CHARS // 2)
    right = min(len(value), end + MAX_CONTEXT_CHARS // 2)
    return re.sub(r"\s+", " ", _plain_text(value[left:right])).strip()


def _reference_kind(reference: str) -> str:
    lowered = reference.lower()
    if "/docx/" in lowered or lowered.startswith("doc-id:"):
        return "feishu_doc"
    if "/wiki/" in lowered:
        return "feishu_wiki"
    if "/sheets/" in lowered:
        return "feishu_sheet"
    if "/base/" in lowered or "/bitable/" in lowered:
        return "feishu_base"
    if "/minutes/" in lowered:
        return "feishu_minutes"
    return "url"


def _dependency_candidates(texts: Iterable[str]) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for value in texts:
        for match in URL_RE.finditer(value):
            reference = match.group(0).rstrip(".,;:!?)】）》」'")
            context = _context(value, match.start(), match.end())
            item = found.setdefault(
                reference,
                {
                    "candidate_id": "dep-"
                    + hashlib.sha256(reference.encode("utf-8")).hexdigest()[:12],
                    "reference_kind": _reference_kind(reference),
                    "reference": reference,
                    "contexts": [],
                    "relationship_hint": False,
                },
            )
            if context and context not in item["contexts"] and len(item["contexts"]) < 3:
                item["contexts"].append(context)
            item["relationship_hint"] = bool(
                item["relationship_hint"] or DEPENDENCY_HINT_RE.search(context)
            )
        for match in DOC_ID_RE.finditer(value):
            reference = "doc-id:" + match.group(1)
            context = _context(value, match.start(), match.end())
            item = found.setdefault(
                reference,
                {
                    "candidate_id": "dep-"
                    + hashlib.sha256(reference.encode("utf-8")).hexdigest()[:12],
                    "reference_kind": "feishu_doc",
                    "reference": reference,
                    "contexts": [],
                    "relationship_hint": False,
                },
            )
            if context and context not in item["contexts"] and len(item["contexts"]) < 3:
                item["contexts"].append(context)
            item["relationship_hint"] = bool(
                item["relationship_hint"] or DEPENDENCY_HINT_RE.search(context)
            )
    return sorted(found.values(), key=lambda item: item["candidate_id"])


def _headings(text: str) -> list[str]:
    values = [
        _plain_text(match.group(1)) for match in HTML_HEADING_RE.finditer(text)
    ]
    values.extend(
        _plain_text(match.group(1)) for match in MARKDOWN_HEADING_RE.finditer(text)
    )
    return list(dict.fromkeys(value for value in values if value))


def _atomic_json(path: Path, value: Mapping[str, Any]) -> int:
    parent = path.parent
    if parent.is_symlink():
        raise DigestAnalysisError(
            "DIGEST_ANALYSIS_PATH_UNSAFE",
            "analysis packet 父目录不能是符号链接。",
            details={"path": str(parent)},
        )
    parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise DigestAnalysisError(
            "DIGEST_ANALYSIS_PATH_UNSAFE",
            "analysis packet 目标不能是符号链接。",
            details={"path": str(path)},
        )
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=".digest-analysis-", dir=parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return len(payload.encode("utf-8"))


def _existing_packet(path: Path, input_hash: str) -> Mapping[str, Any] | None:
    if not path.is_file() or path.is_symlink():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (
        isinstance(value, Mapping)
        and value.get("schema_version") == ANALYSIS_PACKET_SCHEMA
        and value.get("input_hash") == input_hash
    ):
        return value
    return None


def _receipt(path: Path, packet: Mapping[str, Any], *, cache_hit: bool) -> dict[str, Any]:
    stats = packet["stats"]
    return {
        "schema_version": ANALYSIS_PACKET_SCHEMA,
        "packet_path": str(path),
        "input_hash": packet["input_hash"],
        "cache_hit": cache_hit,
        "component_count": stats["component_count"],
        "input_bytes": stats["input_bytes"],
        "output_bytes": path.stat().st_size,
        "dependency_candidate_count": stats["dependency_candidate_count"],
        "heading_count": stats["heading_count"],
        "semantic_item_count": stats["semantic_item_count"],
        "participant_count": stats["participant_count"],
        "anchor_count": stats["anchor_count"],
        **({"meego_changes": packet["meego_changes"]} if "meego_changes" in packet else {}),
    }


def prepare_analysis_packet(
    bundle_path: Path,
    output_path: Path,
    *,
    skill_root: Path = DEFAULT_SKILL_ROOT,
    kb: Path | None = None,
) -> dict[str, Any]:
    output = _outside_skill(output_path, skill_root=skill_root)
    try:
        bundle = load_source_bundle(bundle_path, skill_root=skill_root)
    except SourceBundleError as exc:
        raise DigestAnalysisError(
            exc.code,
            str(exc),
            hint=exc.hint,
            details={"path": exc.path} if exc.path else None,
        ) from exc
    protected_paths = {bundle_path.expanduser().resolve(strict=False)}
    protected_paths.update(
        Path(component.path).resolve(strict=False) for component in bundle.components
    )
    if output in protected_paths:
        raise DigestAnalysisError(
            "DIGEST_ANALYSIS_OUTPUT_COLLISION",
            "analysis packet 不能覆盖 SourceBundle 或 component。",
            details={"path": str(output)},
        )

    sections = []
    component_hashes: dict[str, str] = {}
    component_payloads: dict[str, bytes] = {}
    total_bytes = 0
    for component in bundle.components:
        raw, size, digest = _read_component_bytes(component)
        total_bytes += size
        if total_bytes > MAX_TOTAL_BYTES:
            raise DigestAnalysisError(
                "DIGEST_ANALYSIS_INPUT_TOO_LARGE",
                f"components 合计超过 {MAX_TOTAL_BYTES} bytes。",
            )
        component_hashes[component.name] = digest
        component_payloads[component.name] = raw

    meego_diff = None
    meego_snapshot = None
    if kb is not None and bundle.identity.source_type == "meego":
        component = next((item for item in bundle.components if item.kind == "records"), None)
        if component is not None:
            meego_snapshot = _decode_component(component, component_payloads[component.name])
            if not isinstance(meego_snapshot, Mapping) or meego_snapshot.get("source_type") != "meego":
                raise DigestAnalysisError("DIGEST_ANALYSIS_COMPONENT_INVALID", "Meego records component 必须是 Meego 快照。")
            try:
                meego_diff = diff_current_against_kb(
                    {"snapshot": meego_snapshot}, kb, source_uid=bundle.identity.source_uid,
                )
            except SourceCaptureError as exc:
                raise DigestAnalysisError(exc.code, str(exc), hint=exc.hint, details=exc.details) from exc

    input_hash = canonical_sha256(
        {
            "analysis_algorithm_version": ANALYSIS_ALGORITHM_VERSION,
            "bundle": bundle.to_dict(),
            "component_hashes": component_hashes,
            **({"meego_diff_hash": meego_diff["diff_hash"]} if meego_diff is not None else {}),
        }
    )
    existing = _existing_packet(output, input_hash)
    if existing is not None:
        os.chmod(output, 0o600)
        return _receipt(output, existing, cache_hit=True)

    selected_meego_ids = (
        {item["record_id"] for item in meego_diff["changes"] if "after" in item}
        if meego_diff is not None else None
    )
    reference_texts: list[str] = []
    body_texts: list[str] = []
    participant_ids: set[str] = set()
    semantic_item_count = 0
    for component in bundle.components:
        is_meego_records = bundle.identity.source_type == "meego" and component.kind == "records"
        value = meego_snapshot if is_meego_records and meego_diff is not None else _decode_component(
            component, component_payloads[component.name],
        )
        size = len(component_payloads[component.name])
        if isinstance(value, str):
            text = _plain_text(value)
            items = [{"path": "$", "text": text}] if text else []
            if component.kind == "body":
                body_texts.append(value)
        else:
            if is_meego_records:
                if not isinstance(value, Mapping) or not isinstance(value.get("records"), list):
                    raise DigestAnalysisError(
                        "DIGEST_ANALYSIS_COMPONENT_INVALID", "Meego snapshot 必须包含 records 数组。"
                    )
                items = []
                for index, record in enumerate(value["records"]):
                    if not isinstance(record, Mapping):
                        raise DigestAnalysisError(
                            "DIGEST_ANALYSIS_COMPONENT_INVALID", "Meego record 必须是对象。"
                        )
                    if selected_meego_ids is not None and meego_record_id(record) not in selected_meego_ids:
                        continue
                    items.append({
                        "path": f"$/records/{index}",
                        "text": json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    })
            else:
                items = _dedupe_items(_walk_text(value))
        if is_meego_records and selected_meego_ids is not None:
            serialized = "\n".join(item["text"] for item in items)
        else:
            serialized = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        reference_texts.append(serialized)
        participant_ids.update(OPEN_ID_RE.findall(serialized))
        semantic_item_count += len(items)
        sections.append(
            {
                "name": component.name,
                "kind": component.kind,
                "heading": component.heading or "",
                "source_bytes": size,
                "text_items": items,
            }
        )

    dependencies = _dependency_candidates(reference_texts)
    outline = list(
        dict.fromkeys(
            heading
            for body in body_texts
            for heading in _headings(body)
        )
    )
    selected_anchor_ids = {f"workitem:{record_id}" for record_id in selected_meego_ids or ()}
    anchors = [
        {
            key: anchor[key]
            for key in (
                "anchor_id",
                "kind",
                "precision",
                "component",
                "locator",
                "label",
                "quote",
            )
            if key in anchor
        }
        for anchor in bundle.anchors
        if selected_meego_ids is None or anchor.get("kind") != "meego_workitem"
        or anchor.get("anchor_id") in selected_anchor_ids
    ]
    packet = {
        "schema_version": ANALYSIS_PACKET_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "analysis_algorithm_version": ANALYSIS_ALGORITHM_VERSION,
        "input_hash": input_hash,
        "identity": {
            "source_type": bundle.identity.source_type,
            "source_uid": bundle.identity.source_uid,
            "revision": bundle.identity.revision or "",
        },
        "component_hashes": component_hashes,
        "outline": outline,
        "dependency_candidates": dependencies,
        "participant_ids": sorted(participant_ids),
        "sections": sections,
        "anchors": anchors,
        **({"meego_changes": meego_diff["summary"]} if meego_diff is not None else {}),
        "stats": {
            "component_count": len(bundle.components),
            "input_bytes": total_bytes,
            "dependency_candidate_count": len(dependencies),
            "heading_count": len(outline),
            "semantic_item_count": semantic_item_count,
            "participant_count": len(participant_ids),
            "anchor_count": len(anchors),
        },
    }
    _atomic_json(output, packet)
    return _receipt(output, packet, cache_hit=False)
