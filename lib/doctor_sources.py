"""Read-only source contract audit used by the KB doctor.

This module owns checks that cross CaptureProfile, routine raw metadata,
persisted transaction fields, and canonical record indexes.  It deliberately
does not write profiles or migrate capture policy.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping

from digest_txn import PAYLOAD_SCHEMA
from frontmatter import source_window_end
from provenance import anchor_index
from source_profiles import (
    PROFILE_SOURCE_TYPES,
    SourceProfileError,
    profile_relative_path,
    profile_revision,
    validate_profile,
)
from sources.models import (
    RECORD_INDEX_SCHEMA,
    RecordIndexEntry,
    SourceBundleError,
)


SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
JSON_CODE_BLOCK_RE = re.compile(
    r"(?ms)^`{3,}json[^\n]*\n(?P<body>.*?)\n`{3,}\s*$"
)
PROFILE_PATH_RE = re.compile(r"^sources/[A-Za-z0-9._-]+\.json$")


@dataclass(frozen=True)
class SourceAuditFinding:
    severity: str
    code: str
    path: str
    message: str
    repair: str = ""


@dataclass
class SourceAuditResult:
    counts: Dict[str, int]
    findings: List[SourceAuditFinding]


def _list_value(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value not in (None, ""):
        return [str(value).strip()]
    return []


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


class SourceContractDoctor:
    def __init__(
        self,
        kb: Path,
        raws: Mapping[str, Mapping[str, Any]],
        provenance: Mapping[str, Mapping[str, Any]],
    ):
        self.kb = kb.resolve()
        self.raws = raws
        self.provenance = provenance
        self.profiles: Dict[str, Dict[str, Any]] = {}
        self.profile_paths: Dict[str, Dict[str, Any]] = {}
        self.findings: List[SourceAuditFinding] = []
        self.counts = {
            "profiles": 0,
            "routine_sources": 0,
            "legacy_routine_sources": 0,
            "record_indexes": 0,
        }

    def add(
        self,
        severity: str,
        code: str,
        path: str,
        message: str,
        *,
        repair: str = "",
    ) -> None:
        self.findings.append(
            SourceAuditFinding(severity, code, path, message, repair)
        )

    def scan(self) -> SourceAuditResult:
        self.scan_profiles()
        self.scan_current_raw_contracts()
        self.scan_profile_bindings()
        self.scan_routine_profile_coverage()
        self.scan_record_indexes()
        return SourceAuditResult(
            counts=dict(self.counts),
            findings=list(self.findings),
        )

    def scan_profiles(self) -> None:
        directory = self.kb / "sources"
        if not directory.is_dir():
            return
        for path in sorted(directory.glob("*.json")):
            relative = _relative(path, self.kb)
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                self.add(
                    "error",
                    "SOURCE_PROFILE_UNREADABLE",
                    relative,
                    f"无法读取 source profile: {exc}",
                    repair="从知识库本地 git 恢复；不要根据 raw 摘要猜测配置。",
                )
                continue
            try:
                normalized = validate_profile(value)
                expected = profile_relative_path(normalized)
            except SourceProfileError as exc:
                self.add(
                    "error",
                    "SOURCE_PROFILE_INVALID",
                    relative,
                    f"[{exc.code}] {exc}",
                    repair=exc.hint or "显式核对 selector/capture policy 后重新保存 Profile。",
                )
                continue
            if relative != str(expected):
                self.add(
                    "error",
                    "SOURCE_PROFILE_PATH_MISMATCH",
                    relative,
                    f"文件名与 source_uid 不一致，期望 {expected}。",
                    repair="人工核对 source_uid 后重命名；不要同时保留两个副本。",
                )
            source_uid = normalized["source_uid"]
            old = self.profiles.get(source_uid)
            if old is not None:
                self.add(
                    "error",
                    "SOURCE_PROFILE_DUPLICATE_UID",
                    relative,
                    (
                        f"source_uid 与 {old['relative_path']} 重复: "
                        f"{source_uid}"
                    ),
                    repair="保留唯一 operational truth，人工合并后删除重复 Profile。",
                )
                continue
            item = {
                "profile": normalized,
                "relative_path": relative,
                "revision": profile_revision(normalized),
            }
            self.profiles[source_uid] = item
            self.profile_paths[relative] = item
        self.counts["profiles"] = len(self.profiles)

    def scan_current_raw_contracts(self) -> None:
        for raw in self.raws.values():
            frontmatter = raw["frontmatter"]
            if (
                str(frontmatter.get("payload_schema", "")).strip()
                != PAYLOAD_SCHEMA
            ):
                continue
            relative = raw["relative_path"]
            components = _list_value(frontmatter.get("payload_components"))
            if not components:
                self.add(
                    "error",
                    "RAW_PAYLOAD_COMPONENTS_EMPTY",
                    relative,
                    "当前 payload schema 的 payload_components 不能为空。",
                )
            names: List[str] = []
            for index, component in enumerate(components):
                parts = component.split("|")
                if (
                    len(parts) != 3
                    or not parts[0]
                    or not parts[1]
                    or not SHA256_RE.fullmatch(parts[2])
                ):
                    self.add(
                        "error",
                        "RAW_PAYLOAD_COMPONENT_INVALID",
                        relative,
                        (
                            f"payload_components[{index}] 必须是 "
                            "name|kind|sha256:<64 hex>。"
                        ),
                    )
                    continue
                names.append(parts[0])
            duplicates = sorted(
                {name for name in names if names.count(name) > 1}
            )
            if duplicates:
                self.add(
                    "error",
                    "RAW_PAYLOAD_COMPONENT_DUPLICATE",
                    relative,
                    "payload component name 重复: " + ", ".join(duplicates),
                )

            source_type = str(frontmatter.get("source_type", "")).strip()
            source_uid = str(frontmatter.get("source_uid", "")).strip()
            period = str(
                frontmatter.get("digest_period")
                or frontmatter.get("source_window")
                or ""
            ).strip()
            content_hash = str(
                frontmatter.get("content_hash", "")
            ).strip()
            digest_key = str(frontmatter.get("digest_key", "")).strip()
            if source_type and source_uid and content_hash:
                expected = (
                    f"{source_type}:{source_uid}:{period or '-'}:"
                    f"{content_hash}"
                )
                if digest_key != expected:
                    self.add(
                        "error",
                        "RAW_DIGEST_KEY_MISMATCH",
                        relative,
                        (
                            "digest_key 与 source identity、周期和 "
                            "content_hash 不一致。"
                        ),
                        repair=(
                            "核对事务 receipt 或从原始来源重新摄取；"
                            "不要手改 digest_key。"
                        ),
                    )

    @staticmethod
    def _raw_source_key(frontmatter: Mapping[str, Any]) -> str:
        source_uid = str(frontmatter.get("source_uid", "")).strip()
        if source_uid:
            return source_uid
        source_type = str(frontmatter.get("source_type", "")).strip()
        if source_type == "feishu_chat":
            return str(
                frontmatter.get("source_chat_id")
                or frontmatter.get("source_chat_name")
                or frontmatter.get("raw_id")
                or ""
            ).strip()
        return str(
            frontmatter.get("source_url")
            or frontmatter.get("source_title")
            or frontmatter.get("raw_id")
            or ""
        ).strip()

    @staticmethod
    def _raw_last_seen(frontmatter: Mapping[str, Any]) -> str:
        if str(frontmatter.get("source_type", "")).strip() == "feishu_chat":
            return source_window_end(
                str(frontmatter.get("source_window", "")).strip()
            )
        period = str(frontmatter.get("digest_period", "")).strip()
        if period:
            return period
        return str(frontmatter.get("ingested", "")).strip()

    def scan_profile_bindings(self) -> None:
        for raw in self.raws.values():
            frontmatter = raw["frontmatter"]
            relative = raw["relative_path"]
            profile_path = str(
                frontmatter.get("source_profile_path", "")
            ).strip()
            revision = str(
                frontmatter.get("source_profile_revision", "")
            ).strip()
            if not profile_path and not revision:
                continue
            if not profile_path or not revision:
                self.add(
                    "error",
                    "RAW_PROFILE_BINDING_INCOMPLETE",
                    relative,
                    (
                        "source_profile_path 与 source_profile_revision "
                        "必须同时存在。"
                    ),
                    repair="从事务 receipt 或本地 git 恢复完整绑定。",
                )
                continue
            if not PROFILE_PATH_RE.fullmatch(profile_path):
                self.add(
                    "error",
                    "RAW_PROFILE_PATH_INVALID",
                    relative,
                    "source_profile_path 必须指向 KB sources/ 下的 JSON 文件。",
                )
                continue
            if not SHA256_RE.fullmatch(revision):
                self.add(
                    "error",
                    "RAW_PROFILE_REVISION_INVALID",
                    relative,
                    "source_profile_revision 不是 canonical sha256。",
                )
            profile = self.profile_paths.get(profile_path)
            if profile is None:
                self.add(
                    "warning",
                    "RAW_PROFILE_REFERENCE_MISSING",
                    relative,
                    f"引用的当前 Profile 不存在或无效: {profile_path}。",
                    repair=(
                        "先查知识库本地 git；历史 revision 可保留，"
                        "不要根据 raw 摘要重建配置。"
                    ),
                )
                continue
            normalized = profile["profile"]
            raw_uid = str(frontmatter.get("source_uid", "")).strip()
            raw_type = str(frontmatter.get("source_type", "")).strip()
            if (
                normalized["source_uid"] != raw_uid
                or normalized["source_type"] != raw_type
            ):
                self.add(
                    "error",
                    "RAW_PROFILE_IDENTITY_MISMATCH",
                    relative,
                    "raw 的 source identity 与引用的 Profile 不一致。",
                    repair=(
                        "核对原始事务和本地 git；不要改写历史 raw "
                        "来迎合当前 Profile。"
                    ),
                )

    def scan_routine_profile_coverage(self) -> None:
        legacy: Dict[str, Dict[str, Any]] = {}
        for raw in self.raws.values():
            frontmatter = raw["frontmatter"]
            cadence = str(frontmatter.get("routine", "")).strip()
            if not cadence:
                continue
            key = self._raw_source_key(frontmatter)
            if not key:
                continue
            candidate = {
                "source_type": str(
                    frontmatter.get("source_type", "")
                ).strip(),
                "source_uid": str(
                    frontmatter.get("source_uid", "")
                ).strip(),
                "cadence": cadence,
                "last_seen": self._raw_last_seen(frontmatter),
                "relative_path": raw["relative_path"],
            }
            old = legacy.get(key)
            if old is None or candidate["last_seen"] > old["last_seen"]:
                legacy[key] = candidate

        profile_uids = set(self.profiles)
        uncovered = {
            key: item
            for key, item in legacy.items()
            if key not in profile_uids
        }
        enabled_profiles = sum(
            bool(item["profile"]["routine"]["enabled"])
            for item in self.profiles.values()
        )
        self.counts["legacy_routine_sources"] = len(uncovered)
        self.counts["routine_sources"] = len(uncovered) + enabled_profiles

        for item in uncovered.values():
            source_type = item["source_type"]
            path = item["relative_path"]
            if not item["source_uid"]:
                self.add(
                    "warning",
                    "ROUTINE_SOURCE_UID_MISSING",
                    path,
                    (
                        "定期来源缺少稳定 source_uid，无法与未来 Profile "
                        "确定性合并。"
                    ),
                    repair="迁移前先从原始 URL/来源 API 核对稳定 identity。",
                )
            if source_type in {
                "meego",
                "aeolus",
                "feishu_base",
                "feishu_chat",
            }:
                repair = {
                    "aeolus": "重新 source register，显式保存看板口径。",
                    "feishu_base": (
                        "先 source inspect 核对视图和字段，再用 "
                        "source profile-save 保存 v2 Profile。"
                    ),
                    "feishu_chat": (
                        "先用 pull-chat.sh 核对 chat_id 和首个窗口，再用 "
                        "source profile-save 保存增量策略。"
                    ),
                    "meego": (
                        "先 source inspect 核对视图和字段，再用 "
                        "source profile-save 保存 v2 Profile。"
                    ),
                }[source_type]
                self.add(
                    "error",
                    "ROUTINE_SOURCE_PROFILE_REQUIRED",
                    path,
                    (
                        f"{source_type} 定期来源必须由 Profile 重放配置，"
                        "不能从最近 raw 猜测。"
                    ),
                    repair=repair,
                )
            elif source_type == "feishu_doc":
                self.add(
                    "warning",
                    "ROUTINE_SOURCE_PROFILE_MISSING",
                    path,
                    "飞书文档定期来源仍在使用历史 raw 兼容配置。",
                    repair=(
                        "核对稳定 identity 与 capture policy，"
                        "再通过 source profile-save 显式保存 v2 Profile。"
                    ),
                )
            elif source_type not in PROFILE_SOURCE_TYPES:
                self.add(
                    "info",
                    "ROUTINE_SOURCE_PROFILE_UNSUPPORTED",
                    path,
                    (
                        f"{source_type or 'unknown'} 尚无 Profile schema，"
                        "继续按 legacy routine 兼容运行。"
                    ),
                )

    def scan_record_indexes(self) -> None:
        for raw_id, raw in self.raws.items():
            documents = []
            for match in JSON_CODE_BLOCK_RE.finditer(raw["body"]):
                try:
                    value = json.loads(match.group("body"))
                except json.JSONDecodeError:
                    continue
                if (
                    isinstance(value, Mapping)
                    and value.get("schema_version") == RECORD_INDEX_SCHEMA
                ):
                    documents.append(value)
            if not documents:
                continue
            relative = raw["relative_path"]
            if len(documents) != 1:
                self.add(
                    "error",
                    "RAW_RECORD_INDEX_DUPLICATE",
                    relative,
                    "同一 raw 中只能有一份规范记录索引。",
                )
                continue
            document = documents[0]
            unknown = sorted(
                set(document)
                - {"schema_version", "source_type", "source_uid", "records"}
            )
            if unknown:
                self.add(
                    "error",
                    "RAW_RECORD_INDEX_INVALID",
                    relative,
                    "规范记录索引含未知字段: " + ", ".join(unknown),
                )
                continue
            records = document.get("records")
            if not isinstance(records, list):
                self.add(
                    "error",
                    "RAW_RECORD_INDEX_INVALID",
                    relative,
                    "规范记录索引 records 必须是数组。",
                )
                continue
            frontmatter = raw["frontmatter"]
            if (
                str(document.get("source_type", "")).strip()
                != str(frontmatter.get("source_type", "")).strip()
                or str(document.get("source_uid", "")).strip()
                != str(frontmatter.get("source_uid", "")).strip()
            ):
                self.add(
                    "error",
                    "RAW_RECORD_INDEX_IDENTITY_MISMATCH",
                    relative,
                    "规范记录索引与 raw 的 source identity 不一致。",
                )
                continue
            sidecar = self.provenance.get(raw_id)
            anchor_ids = (
                set(anchor_index(sidecar["document"]))
                if sidecar is not None
                else set()
            )
            record_ids: List[str] = []
            try:
                for index, value in enumerate(records):
                    item = RecordIndexEntry.from_dict(
                        value,
                        index=index,
                        anchor_ids=anchor_ids,
                    )
                    record_ids.append(item.record_id)
            except SourceBundleError as exc:
                self.add(
                    "error",
                    "RAW_RECORD_INDEX_INVALID",
                    relative,
                    f"[{exc.code}] {exc}",
                )
                continue
            duplicates = sorted(
                {
                    record_id
                    for record_id in record_ids
                    if record_ids.count(record_id) > 1
                }
            )
            if duplicates:
                self.add(
                    "error",
                    "RAW_RECORD_INDEX_DUPLICATE_ID",
                    relative,
                    "规范记录索引 record_id 重复: " + ", ".join(duplicates),
                )
                continue
            self.counts["record_indexes"] += 1


def scan_source_contracts(
    kb: Path,
    raws: Mapping[str, Mapping[str, Any]],
    provenance: Mapping[str, Mapping[str, Any]],
) -> SourceAuditResult:
    return SourceContractDoctor(kb, raws, provenance).scan()
