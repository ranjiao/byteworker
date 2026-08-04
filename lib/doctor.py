"""Read-only compatibility audit and bounded repairs for a byteworker KB."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping

from constants import NODE_ID_PREFIXES, NODE_TYPES
from digest_txn import ALLOWED_SOURCE_TYPES, PAYLOAD_SCHEMA
from doctor_sources import scan_source_contracts
from frontmatter import extract_tldr, extract_title, parse_file
from provenance import (
    EVIDENCE_MARKER_RE,
    ProvenanceError,
    anchor_index,
    load_provenance,
)


SCHEMA_PROFILE = "byteworker-kb/v1"
ALLOWED_NODE_STATUS = {"current", "stale", "superseded"}
ALLOWED_THINKING_STATUS = {"effective", "inactive"}
ALLOWED_RAW_STATUS = {"pending", "digested", "failed"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})$"
)
NODE_ID_RE = re.compile(
    r"\b(?:person|project|area|org|event|decision|reading|thinking)-"
    r"[A-Za-z0-9._-]+\b"
)
RAW_ID_RE = re.compile(r"^raw-[A-Za-z0-9._-]+$")
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
EVIDENCE_ROW_RE = re.compile(
    r"^\|\s*\*\*\[(E[1-9][0-9]*)\]\*\*\s*\|(?P<rest>.*)$"
)
REPORT_MARKER_RE = re.compile(r"\[(S[1-9][0-9]*)\]")
REPORT_CITATION_RE = re.compile(
    r"^\s*-\s+(?:\*\*)?\[(S[1-9][0-9]*)\](?:\*\*)?(?:\s|$)"
)

NODE_REQUIRED_FIELDS = (
    "id",
    "title",
    "type",
    "tags",
    "status",
    "created",
    "updated",
    "last_verified",
    "sources",
    "links",
)
THINKING_REQUIRED_FIELDS = (
    "id",
    "title",
    "type",
    "status",
    "created",
    "updated",
)
NODE_LIST_FIELDS = ("tags", "sources", "links")
CURRENT_RAW_REQUIRED_FIELDS = (
    "raw_id",
    "ingested",
    "source_type",
    "source_uid",
    "payload_schema",
    "payload_components",
    "content_hash",
    "digest_key",
    "digest_status",
    "digest_targets",
)
EXPECTED_DIRS = (
    "sources",
    "raw_data",
    "provenance",
    "journal",
    "reports/daily",
    "reports/weekly",
    "reports/im",
    "knowledge/people",
    "knowledge/projects",
    "knowledge/areas",
    "knowledge/orgs",
    "knowledge/events",
    "knowledge/decisions",
    "knowledge/readings",
)
TRUTH_FILES = ("context.md", "todo.md", "dashboard.md")
SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}
SCHEMA_CONTRACT_PATHS = (
    "DESIGN.md",
    "lib/constants.py",
    "lib/digest_txn.py",
    "lib/provenance.py",
    "lib/source_profiles.py",
    "lib/sources/models.py",
    "lib/sources/transaction_bridge.py",
)


@dataclass(frozen=True)
class Finding:
    severity: str
    code: str
    path: str
    message: str
    repair: str = ""
    auto_fix: str = ""


@dataclass
class DoctorReport:
    kb: str
    schema_profile: str
    schema_fingerprint: str
    counts: Dict[str, int]
    findings: List[Finding]

    def summary(self) -> Dict[str, int]:
        result = {"error": 0, "warning": 0, "info": 0, "auto_fixable": 0}
        for item in self.findings:
            result[item.severity] += 1
            if item.auto_fix:
                result["auto_fixable"] += 1
        return result

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_profile": self.schema_profile,
            "schema_fingerprint": self.schema_fingerprint,
            "kb": self.kb,
            "counts": self.counts,
            "summary": self.summary(),
            "findings": [asdict(item) for item in self.findings],
        }


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


def _frontmatter_state(path: Path) -> tuple[bool, bool, List[str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return False, False, []
    end = next(
        (idx for idx, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
        None,
    )
    if end is None:
        return True, False, []
    keys: List[str] = []
    for line in lines[1:end]:
        if line and not line[0].isspace() and ":" in line:
            keys.append(line.partition(":")[0].strip())
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    return True, True, duplicates


def schema_fingerprint(skill_root: Path) -> str:
    digest = hashlib.sha256()
    files = [
        *(skill_root / relative for relative in SCHEMA_CONTRACT_PATHS),
        *sorted((skill_root / "templates").glob("node-*.md")),
        *sorted((skill_root / "templates").glob("report-*.md")),
        *sorted((skill_root / "templates").glob("digest-*.json")),
        *sorted((skill_root / "templates").glob("source-*.json")),
    ]
    for path in files:
        digest.update(str(path.relative_to(skill_root)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()[:16]


def _required_headings(skill_root: Path) -> Dict[str, List[str]]:
    result: Dict[str, List[str]] = {}
    for _, node_type, _ in NODE_TYPES:
        template = skill_root / "templates" / f"node-{node_type}.md"
        text = template.read_text(encoding="utf-8")
        result[node_type] = re.findall(r"(?m)^##\s+(.+?)\s*$", text)
    return result


class Doctor:
    def __init__(self, kb: Path, skill_root: Path):
        self.kb = kb.resolve()
        self.skill_root = skill_root.resolve()
        self.findings: List[Finding] = []
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.raws: Dict[str, Dict[str, Any]] = {}
        self.provenance: Dict[str, Dict[str, Any]] = {}
        self.counts = {
            "nodes": 0,
            "raws": 0,
            "provenance": 0,
            "reports": 0,
            "profiles": 0,
            "routine_sources": 0,
            "legacy_routine_sources": 0,
            "record_indexes": 0,
        }
        self.required_headings = _required_headings(self.skill_root)
        self.legacy_raw_paths: List[str] = []

    def add(
        self,
        severity: str,
        code: str,
        path: str,
        message: str,
        *,
        repair: str = "",
        auto_fix: str = "",
    ) -> None:
        self.findings.append(
            Finding(severity, code, path, message, repair, auto_fix)
        )

    def scan(self) -> DoctorReport:
        self.scan_layout()
        self.scan_raws()
        self.scan_nodes()
        self.scan_provenance()
        source_audit = scan_source_contracts(
            self.kb,
            self.raws,
            self.provenance,
        )
        self.counts.update(source_audit.counts)
        for item in source_audit.findings:
            self.add(
                item.severity,
                item.code,
                item.path,
                item.message,
                repair=item.repair,
            )
        self.scan_cross_references()
        self.scan_links()
        self.scan_reports()
        self.scan_index()
        if self.legacy_raw_paths:
            sample = ", ".join(self.legacy_raw_paths[:5])
            extra = len(self.legacy_raw_paths) - 5
            if extra > 0:
                sample += f" 等 {len(self.legacy_raw_paths)} 个"
            self.add(
                "info",
                "RAW_LEGACY_COMPATIBLE",
                "raw_data/",
                (
                    f"{len(self.legacy_raw_paths)} 个历史 raw 没有 {PAYLOAD_SCHEMA} "
                    f"标记；按兼容策略保留，不要求改写原文。样例: {sample}"
                ),
            )
        self.findings.sort(
            key=lambda item: (
                SEVERITY_ORDER[item.severity],
                item.code,
                item.path,
                item.message,
            )
        )
        return DoctorReport(
            kb=str(self.kb),
            schema_profile=SCHEMA_PROFILE,
            schema_fingerprint=schema_fingerprint(self.skill_root),
            counts=dict(self.counts),
            findings=list(self.findings),
        )

    def scan_layout(self) -> None:
        if not self.kb.is_dir():
            self.add("error", "KB_NOT_FOUND", str(self.kb), "知识库目录不存在。")
            return
        try:
            self.kb.relative_to(self.skill_root)
            inside_skill = True
        except ValueError:
            inside_skill = False
        if inside_skill:
            self.add(
                "error",
                "KB_INSIDE_SKILL_REPO",
                ".",
                "知识库数据位于 skill 仓库内，违反逻辑/数据隔离。",
                repair="迁移到独立目录并更新 .kbconfig。",
            )
        for relative in EXPECTED_DIRS:
            if not (self.kb / relative).is_dir():
                self.add(
                    "error",
                    "LAYOUT_MISSING_DIRECTORY",
                    relative,
                    "缺少当前 schema 需要的目录；不要先创建空目录掩盖可能的数据丢失。",
                    repair="先从知识库本地 git 核对/恢复；确认是新目录后再创建。",
                )
        for relative in TRUTH_FILES:
            if not (self.kb / relative).is_file():
                self.add(
                    "error",
                    "TRUTH_FILE_MISSING",
                    relative,
                    "不可派生的真相源文件缺失。",
                    repair="优先从知识库本地 git 恢复；不要由 doctor 自动生成空文件。",
                )
        if not (self.kb / ".git").exists():
            self.add(
                "warning",
                "KB_GIT_MISSING",
                ".git",
                "知识库没有本地 Git 回滚网。",
                repair="确认目录正确后执行 git init 并建立初始提交。",
            )
        else:
            result = subprocess.run(
                ["git", "-C", str(self.kb), "remote"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                self.add(
                    "error",
                    "KB_GIT_HAS_REMOTE",
                    ".git/config",
                    "知识库本地 Git 配置了 remote，违反数据目录永不外传约束。",
                    repair="人工核对后移除 remote；doctor 不自动修改 Git 配置。",
                )
        for path in sorted(self.kb.glob("**/*.tmp")):
            self.add(
                "warning",
                "STALE_TEMP_FILE",
                _relative(path, self.kb),
                "发现事务临时文件，可能是中断写入残留。",
                repair="与目标文件及本地 git 核对后人工删除或恢复。",
            )

    def _markdown(self, path: Path, kind: str) -> tuple[Dict[str, Any], str] | None:
        relative = _relative(path, self.kb)
        try:
            has_open, has_close, duplicate_keys = _frontmatter_state(path)
        except (OSError, UnicodeDecodeError) as exc:
            self.add("error", f"{kind}_UNREADABLE", relative, f"无法读取: {exc}")
            return None
        if not has_open:
            self.add("error", f"{kind}_NO_FRONTMATTER", relative, "缺少 frontmatter。")
            return None
        if not has_close:
            self.add(
                "error",
                f"{kind}_UNCLOSED_FRONTMATTER",
                relative,
                "frontmatter 未闭合。",
            )
            return None
        if duplicate_keys:
            self.add(
                "error",
                f"{kind}_DUPLICATE_FRONTMATTER_KEY",
                relative,
                "frontmatter 存在重复字段: " + ", ".join(duplicate_keys),
            )
        return parse_file(str(path))

    def scan_raws(self) -> None:
        directory = self.kb / "raw_data"
        if not directory.is_dir():
            return
        for path in sorted(directory.glob("*.md")):
            parsed = self._markdown(path, "RAW")
            if parsed is None:
                continue
            frontmatter, body = parsed
            relative = _relative(path, self.kb)
            raw_id = str(frontmatter.get("raw_id", "")).strip()
            if not raw_id:
                raw_id = (
                    path.stem if path.stem.startswith("raw-") else "raw-" + path.stem
                )
                self.add(
                    "warning",
                    "RAW_LEGACY_MISSING_ID",
                    relative,
                    f"历史 raw 缺少 raw_id；扫描时按文件名兼容识别为 {raw_id}。",
                    repair="若要回填，先核对所有节点 sources 与本地 git；doctor 不改写 raw。",
                )
            if not RAW_ID_RE.fullmatch(raw_id):
                self.add("error", "RAW_INVALID_ID", relative, f"raw_id 非法: {raw_id}")
            if raw_id in self.raws:
                self.add(
                    "error",
                    "RAW_DUPLICATE_ID",
                    relative,
                    f"raw_id 与 {self.raws[raw_id]['relative_path']} 重复: {raw_id}",
                )
                continue
            expected_name = raw_id.removeprefix("raw-") + ".md"
            if path.name != expected_name:
                self.add(
                    "warning",
                    "RAW_FILENAME_MISMATCH",
                    relative,
                    f"文件名应与 raw_id 对应，期望 {expected_name}。",
                    repair="人工改名并同步所有路径引用；doctor 不自动级联重命名。",
                )
            payload_schema = str(frontmatter.get("payload_schema", "")).strip()
            if not payload_schema:
                self.legacy_raw_paths.append(relative)
            elif payload_schema != PAYLOAD_SCHEMA:
                self.add(
                    "error",
                    "RAW_UNSUPPORTED_PAYLOAD_SCHEMA",
                    relative,
                    f"payload_schema={payload_schema!r}，当前只支持 {PAYLOAD_SCHEMA}。",
                )
            else:
                missing = [
                    key for key in CURRENT_RAW_REQUIRED_FIELDS if key not in frontmatter
                ]
                if missing:
                    self.add(
                        "error",
                        "RAW_CURRENT_SCHEMA_MISSING_FIELDS",
                        relative,
                        "当前 payload schema 缺少字段: " + ", ".join(missing),
                    )
            source_type = str(frontmatter.get("source_type", "")).strip()
            if source_type and source_type not in ALLOWED_SOURCE_TYPES:
                self.add(
                    "error",
                    "RAW_INVALID_SOURCE_TYPE",
                    relative,
                    f"source_type={source_type!r} 不受当前代码支持。",
                )
            ingested = str(frontmatter.get("ingested", "")).strip()
            if ingested and not (TIMESTAMP_RE.fullmatch(ingested) or DATE_RE.fullmatch(ingested)):
                self.add(
                    "warning",
                    "RAW_INVALID_INGESTED_TIME",
                    relative,
                    f"ingested 时间格式不规范: {ingested}",
                )
            digest_status = str(frontmatter.get("digest_status", "")).strip()
            if digest_status and digest_status not in ALLOWED_RAW_STATUS:
                self.add(
                    "error",
                    "RAW_INVALID_DIGEST_STATUS",
                    relative,
                    f"digest_status={digest_status!r} 非法。",
                )
            elif digest_status in {"pending", "failed"}:
                self.add(
                    "warning",
                    "RAW_INCOMPLETE_DIGEST",
                    relative,
                    f"digest_status={digest_status}，需要恢复或重新摄取。",
                    repair="核对原始来源后重新执行 digest；不要仅把状态改成 digested。",
                )
            content_hash = str(frontmatter.get("content_hash", "")).strip()
            if content_hash and not SHA256_RE.fullmatch(content_hash):
                self.add(
                    "error" if payload_schema == PAYLOAD_SCHEMA else "warning",
                    "RAW_INVALID_CONTENT_HASH",
                    relative,
                    "content_hash 不是 sha256:<64 hex>。",
                )
            if not body.strip():
                if payload_schema == PAYLOAD_SCHEMA:
                    self.add("error", "RAW_EMPTY_BODY", relative, "raw 正文为空。")
                else:
                    self.add(
                        "warning",
                        "RAW_LEGACY_EMPTY_BODY",
                        relative,
                        "历史 raw 只有运维元数据、没有逐字正文，不能作为事实证据。",
                        repair="优先从原始来源或本地 git 恢复；不要凭摘要反向生成 raw。",
                    )
            self.raws[raw_id] = {
                "path": path,
                "relative_path": relative,
                "frontmatter": frontmatter,
                "body": body,
            }
        self.counts["raws"] = len(self.raws)

    def scan_nodes(self) -> None:
        directory = self.kb / "knowledge"
        if not directory.is_dir():
            return
        dir_to_type = {dir_name: node_type for dir_name, node_type, _ in NODE_TYPES}
        for path in sorted(directory.glob("**/*.md")):
            parsed = self._markdown(path, "NODE")
            if parsed is None:
                continue
            frontmatter, body = parsed
            relative = _relative(path, self.kb)
            node_id = str(frontmatter.get("id", "")).strip()
            node_type = str(frontmatter.get("type", "")).strip()
            if not node_id:
                self.add("error", "NODE_MISSING_ID", relative, "缺少 id。")
                continue
            if node_id in self.nodes:
                self.add(
                    "error",
                    "NODE_DUPLICATE_ID",
                    relative,
                    f"id 与 {self.nodes[node_id]['relative_path']} 重复: {node_id}",
                )
                continue
            required_fields = (
                THINKING_REQUIRED_FIELDS
                if node_type == "thinking"
                else NODE_REQUIRED_FIELDS
            )
            missing = [field for field in required_fields if field not in frontmatter]
            if missing:
                self.add(
                    "error",
                    "NODE_MISSING_FIELDS",
                    relative,
                    "缺少当前节点 schema 字段: " + ", ".join(missing),
                )
            path_parts = path.relative_to(directory).parts
            path_type = dir_to_type.get(path_parts[0], "") if path_parts else ""
            if not path_type:
                self.add(
                    "error",
                    "NODE_UNKNOWN_DIRECTORY",
                    relative,
                    "节点不在固定的 8 类目录中。",
                )
            elif node_type != path_type:
                self.add(
                    "error",
                    "NODE_TYPE_PATH_MISMATCH",
                    relative,
                    f"type={node_type!r}，目录期望 {path_type!r}。",
                )
            if node_type not in {value for _, value, _ in NODE_TYPES}:
                self.add(
                    "error",
                    "NODE_INVALID_TYPE",
                    relative,
                    f"type={node_type!r} 不受当前 schema 支持。",
                )
            if not node_id.startswith(NODE_ID_PREFIXES) or (
                node_type and not node_id.startswith(node_type + "-")
            ):
                self.add(
                    "error",
                    "NODE_ID_TYPE_MISMATCH",
                    relative,
                    f"id={node_id!r} 与 type={node_type!r} 不匹配。",
                )
            if path.name != node_id + ".md":
                self.add(
                    "warning",
                    "NODE_FILENAME_MISMATCH",
                    relative,
                    f"文件名应为 {node_id}.md。",
                    repair="人工改名并核对所有外部路径引用。",
                )
            for field in NODE_LIST_FIELDS:
                if field in frontmatter and not isinstance(frontmatter[field], list):
                    self.add(
                        "warning",
                        "NODE_SCALAR_LIST_FIELD",
                        relative,
                        f"{field} 应使用列表格式。",
                    )
            status = str(frontmatter.get("status", "")).strip()
            allowed_status = (
                ALLOWED_THINKING_STATUS
                if node_type == "thinking"
                else ALLOWED_NODE_STATUS
            )
            if status and status not in allowed_status:
                self.add(
                    "error",
                    "NODE_INVALID_STATUS",
                    relative,
                    f"status={status!r} 非法。",
                )
            for field in ("created", "updated", "last_verified"):
                value = str(frontmatter.get(field, "")).strip()
                if value and not DATE_RE.fullmatch(value):
                    self.add(
                        "warning",
                        "NODE_INVALID_DATE",
                        relative,
                        f"{field}={value!r} 不是 YYYY-MM-DD。",
                    )
            sources = _list_value(frontmatter.get("sources"))
            if (
                node_type != "thinking"
                and "sources" in frontmatter
                and not sources
            ):
                self.add("error", "NODE_EMPTY_SOURCES", relative, "sources 为空。")
            primary = str(frontmatter.get("primary_source", "")).strip()
            if node_type in {"event", "decision", "reading"} and not primary:
                current_sources = [
                    source
                    for source in sources
                    if source in self.raws
                    and str(
                        self.raws[source]["frontmatter"].get("payload_schema", "")
                    ).strip()
                    == PAYLOAD_SCHEMA
                ]
                severity = "error" if current_sources and "## 证据" in body else "warning"
                self.add(
                    severity,
                    (
                        "NODE_PRIMARY_SOURCE_MISSING"
                        if severity == "error"
                        else "NODE_LEGACY_PRIMARY_SOURCE_MISSING"
                    ),
                    relative,
                    (
                        f"{node_type} 主记录缺少 primary_source。"
                        if severity == "error"
                        else (
                            f"历史 {node_type} 主记录没有 primary_source；"
                            "仍可沿 sources 降级读取。"
                        )
                    ),
                    repair=(
                        "核对 sources/raw 后人工选择主来源；多来源歧义时不要自动补。"
                    ),
                )
            if primary and primary not in sources:
                self.add(
                    "error",
                    "NODE_PRIMARY_SOURCE_NOT_IN_SOURCES",
                    relative,
                    f"primary_source={primary} 不在 sources 中。",
                )
            if node_type == "person":
                feishu_id = str(frontmatter.get("feishu_id", "")).strip()
                if not feishu_id or feishu_id == "?":
                    self.add(
                        "warning",
                        "PERSON_FEISHU_ID_UNRESOLVED",
                        relative,
                        "person 缺少可用 feishu_id，属于待回填历史数据。",
                        repair="用 resolve-users/lark-contact 核实后回填；不要改节点 id。",
                    )
                enterprise_email = str(
                    frontmatter.get("enterprise_email", "")
                ).strip()
                department_path = str(
                    frontmatter.get("department_path", "")
                ).strip()
                directory_verified_at = str(
                    frontmatter.get("directory_verified_at", "")
                ).strip()
                if (
                    enterprise_email or department_path
                ) and not directory_verified_at:
                    self.add(
                        "warning",
                        "PERSON_DIRECTORY_UNVERIFIED",
                        relative,
                        "person 已有通讯录字段，但缺少 directory_verified_at。",
                        repair=(
                            "用 resolve-users.sh --format json 重新核验；"
                            "不要按现有正文猜写时间。"
                        ),
                    )
                elif directory_verified_at and not TIMESTAMP_RE.fullmatch(
                    directory_verified_at
                ):
                    self.add(
                        "warning",
                        "PERSON_DIRECTORY_TIMESTAMP_INVALID",
                        relative,
                        "directory_verified_at 不是带时区 ISO8601。",
                        repair="重新运行通讯录解析并使用 resolved_at。",
                    )
                if enterprise_email and "@" not in enterprise_email:
                    self.add(
                        "warning",
                        "PERSON_ENTERPRISE_EMAIL_INVALID",
                        relative,
                        "enterprise_email 不是合法邮箱形态。",
                        repair="重新运行通讯录解析；不可见时删除该可选字段。",
                    )
            body_title = extract_title(body)
            title = str(frontmatter.get("title", "")).strip()
            if not body_title:
                self.add("error", "NODE_BODY_TITLE_MISSING", relative, "正文缺少 # 标题。")
            elif title and body_title != title:
                self.add(
                    "warning",
                    "NODE_TITLE_MISMATCH",
                    relative,
                    "frontmatter.title 与正文标题不一致。",
                )
            if node_type != "thinking" and not extract_tldr(body):
                self.add("warning", "NODE_TLDR_MISSING", relative, "正文缺少 TL;DR。")
            headings = set(re.findall(r"(?m)^##\s+(.+?)\s*$", body))
            required = set(self.required_headings.get(node_type, []))
            missing_headings = sorted(required - headings)
            if missing_headings:
                self.add(
                    "warning",
                    "NODE_MISSING_SECTIONS",
                    relative,
                    "缺少当前模板章节: " + ", ".join(missing_headings),
                    repair=(
                        f"按 templates/node-{node_type}.md 补齐空章节；"
                        "业务内容必须由 Agent/用户判断。"
                    ),
                )
            evidence_rows = self._evidence_rows(body)
            base_body = re.split(r"(?m)^## 证据\s*$", body, maxsplit=1)[0]
            markers = set(EVIDENCE_MARKER_RE.findall(base_body))
            row_ids = set(evidence_rows)
            if markers != row_ids:
                self.add(
                    "error",
                    "NODE_EVIDENCE_MARKER_MISMATCH",
                    relative,
                    f"正文标记={sorted(markers)}，证据表={sorted(row_ids)}。",
                    repair="从原始 raw/provenance 重新物化证据；不要手写伪造。",
                )
            self.nodes[node_id] = {
                "path": path,
                "relative_path": relative,
                "frontmatter": frontmatter,
                "body": body,
                "sources": sources,
                "links": _list_value(frontmatter.get("links")),
                "evidence_rows": evidence_rows,
            }
        self.counts["nodes"] = len(self.nodes)

    @staticmethod
    def _evidence_rows(body: str) -> Dict[str, Dict[str, str]]:
        section_match = re.search(r"(?ms)^## 证据\s*$([\s\S]*)", body)
        if not section_match:
            return {}
        rows: Dict[str, Dict[str, str]] = {}
        for line in section_match.group(1).splitlines():
            match = EVIDENCE_ROW_RE.match(line)
            if not match:
                continue
            rest = match.group("rest").strip().rstrip("|")
            cells = [
                cell.strip()
                for cell in re.split(r"(?<!\\)\|", rest)
            ]
            raw_match = re.search(r"`([^`]+)`", cells[0] if cells else "")
            locator = cells[1] if len(cells) > 1 else ""
            anchor_match = re.search(r"anchor_id=([^\s·]+)", locator)
            rows[match.group(1)] = {
                "raw_id": raw_match.group(1) if raw_match else "",
                "anchor_id": anchor_match.group(1) if anchor_match else "",
            }
        return rows

    def scan_provenance(self) -> None:
        directory = self.kb / "provenance"
        if not directory.is_dir():
            return
        for path in sorted(directory.glob("*.json")):
            relative = _relative(path, self.kb)
            try:
                document = load_provenance(path)
            except ProvenanceError as exc:
                self.add("error", "PROVENANCE_INVALID", relative, str(exc))
                continue
            raw_id = str(document.get("raw_id", "")).strip()
            if raw_id in self.provenance:
                self.add(
                    "error",
                    "PROVENANCE_DUPLICATE_RAW_ID",
                    relative,
                    f"与 {self.provenance[raw_id]['relative_path']} 重复: {raw_id}",
                )
                continue
            if path.name != raw_id + ".json":
                self.add(
                    "error",
                    "PROVENANCE_FILENAME_MISMATCH",
                    relative,
                    f"文件名应为 {raw_id}.json。",
                )
            if raw_id not in self.raws:
                self.add(
                    "error",
                    "PROVENANCE_ORPHAN",
                    relative,
                    f"找不到对应 raw: {raw_id}。",
                )
            raw_path = str(document.get("raw_path", "")).strip()
            raw = self.raws.get(raw_id)
            if raw and raw_path and raw_path != raw["relative_path"]:
                self.add(
                    "warning",
                    "PROVENANCE_RAW_PATH_MISMATCH",
                    relative,
                    f"raw_path={raw_path!r}，实际为 {raw['relative_path']!r}。",
                )
            if "source" not in anchor_index(document):
                self.add(
                    "error",
                    "PROVENANCE_SOURCE_ANCHOR_MISSING",
                    relative,
                    "缺少 source anchor。",
                )
            derived = document.get("derived_from", {})
            derived_hash = (
                str(derived.get("content_hash", "")).strip()
                if isinstance(derived, Mapping)
                else ""
            )
            current_hash = (
                str(raw["frontmatter"].get("content_hash", "")).strip() if raw else ""
            )
            if derived_hash and current_hash and derived_hash != current_hash:
                self.add(
                    "error",
                    "PROVENANCE_CONTENT_HASH_MISMATCH",
                    relative,
                    "derived_from.content_hash 与 raw.content_hash 不一致。",
                    repair="核对 raw 是否被改写；必要时受控重建 sidecar。",
                )
            self.provenance[raw_id] = {
                "path": path,
                "relative_path": relative,
                "document": document,
            }
        self.counts["provenance"] = len(self.provenance)

    def scan_cross_references(self) -> None:
        for raw_id, raw in self.raws.items():
            frontmatter = raw["frontmatter"]
            targets = _list_value(frontmatter.get("digest_targets"))
            for target in targets:
                if target.startswith("reports/"):
                    report_path = self.kb / target
                    if not report_path.is_file():
                        self.add(
                            "warning",
                            "RAW_TARGET_MISSING_REPORT",
                            raw["relative_path"],
                            f"digest_targets 指向不存在报告: {target}",
                        )
                    continue
                node = self.nodes.get(target)
                if not node:
                    if target.startswith(NODE_ID_PREFIXES):
                        self.add(
                            "error",
                            "RAW_TARGET_MISSING_NODE",
                            raw["relative_path"],
                            f"digest_targets 指向不存在节点: {target}",
                        )
                    else:
                        self.add(
                            "warning",
                            "RAW_TARGET_UNKNOWN",
                            raw["relative_path"],
                            f"digest_targets 使用当前 schema 不识别的目标: {target}",
                        )
                elif raw_id not in node["sources"]:
                    self.add(
                        "warning",
                        "RAW_TARGET_NOT_IN_NODE_SOURCES",
                        node["relative_path"],
                        f"raw {raw_id} 指向该节点，但节点 sources 未包含它。",
                    )
            is_current = str(frontmatter.get("payload_schema", "")).strip() == PAYLOAD_SCHEMA
            if (
                is_current
                and str(frontmatter.get("digest_status", "")).strip() == "digested"
                and raw_id not in self.provenance
            ):
                self.add(
                    "error",
                    "RAW_CURRENT_SCHEMA_MISSING_PROVENANCE",
                    raw["relative_path"],
                    "当前事务 raw 已 digested，但缺少 provenance sidecar。",
                    repair="从事务 receipt/抓取 locator 恢复；无法精确恢复时走保守 backfill。",
                )
        for node_id, node in self.nodes.items():
            for source in node["sources"]:
                if source.startswith("raw-") and source not in self.raws:
                    self.add(
                        "warning",
                        "NODE_SOURCE_MISSING_RAW",
                        node["relative_path"],
                        f"sources 指向当前库中不存在的 raw: {source}",
                        repair="核对本地 git/历史归档；找不到时保留缺口并降低引用置信度。",
                    )
            primary = str(node["frontmatter"].get("primary_source", "")).strip()
            if primary.startswith("raw-") and primary not in self.raws:
                self.add(
                    "error",
                    "NODE_PRIMARY_SOURCE_MISSING_RAW",
                    node["relative_path"],
                    f"primary_source 指向不存在 raw: {primary}",
                )
            for marker, row in node["evidence_rows"].items():
                raw_id = row["raw_id"]
                anchor_id = row["anchor_id"]
                if not raw_id or not anchor_id:
                    self.add(
                        "error",
                        "NODE_EVIDENCE_ROW_INCOMPLETE",
                        node["relative_path"],
                        f"{marker} 缺少 raw_id 或 anchor_id。",
                    )
                    continue
                if raw_id not in self.raws:
                    self.add(
                        "error",
                        "NODE_EVIDENCE_RAW_MISSING",
                        node["relative_path"],
                        f"{marker} 指向不存在 raw: {raw_id}",
                    )
                    continue
                sidecar = self.provenance.get(raw_id)
                if not sidecar:
                    self.add(
                        "error",
                        "NODE_EVIDENCE_PROVENANCE_MISSING",
                        node["relative_path"],
                        f"{marker} 的 provenance 不存在: {raw_id}",
                    )
                    continue
                if anchor_id not in anchor_index(sidecar["document"]):
                    self.add(
                        "error",
                        "NODE_EVIDENCE_ANCHOR_MISSING",
                        node["relative_path"],
                        f"{marker} 找不到 anchor: {raw_id}#{anchor_id}",
                    )

    def scan_links(self) -> None:
        for node_id, node in self.nodes.items():
            links = node["links"]
            duplicates = sorted({value for value in links if links.count(value) > 1})
            if duplicates:
                self.add(
                    "warning",
                    "NODE_LINK_DUPLICATE",
                    node["relative_path"],
                    "links 重复: " + ", ".join(duplicates),
                    auto_fix="links",
                )
            for target in dict.fromkeys(links):
                if target == node_id:
                    self.add(
                        "error",
                        "NODE_SELF_LINK",
                        node["relative_path"],
                        f"节点自链接: {node_id}",
                        repair="运行 doctor fix --only links 确定性删除。",
                        auto_fix="links",
                    )
                elif target not in self.nodes:
                    self.add(
                        "error",
                        "NODE_DANGLING_LINK",
                        node["relative_path"],
                        f"悬空 links: {node_id} -> {target}",
                        repair="人工裁决改 id、补建节点或删除链接。",
                    )
                elif node_id not in self.nodes[target]["links"]:
                    self.add(
                        "warning",
                        "NODE_BACKLINK_MISSING",
                        self.nodes[target]["relative_path"],
                        f"缺少反向链接: {target} -> {node_id}",
                        auto_fix="links",
                    )
            body_ids = sorted(set(NODE_ID_RE.findall(node["body"])))
            missing_body_links = [
                target
                for target in body_ids
                if target != node_id
                and target in self.nodes
                and target not in links
            ]
            if missing_body_links:
                self.add(
                    "warning",
                    "NODE_BODY_LINK_MISSING",
                    node["relative_path"],
                    "正文提及但 links 未登记: " + ", ".join(missing_body_links),
                    repair="运行 doctor fix --only links --autolink。",
                    auto_fix="autolink",
                )

    def scan_reports(self) -> None:
        report_root = self.kb / "reports"
        if not report_root.is_dir():
            return
        patterns = {
            "daily": re.compile(r"^\d{4}-\d{2}-\d{2}\.md$"),
            "weekly": re.compile(r"^\d{4}-W\d{2}\.md$"),
            "im": re.compile(
                r"^(?:\d{4}-\d{2}-\d{2}|.+__.+)\.md$"
            ),
        }
        for kind, filename_re in patterns.items():
            directory = report_root / kind
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.md")):
                self.counts["reports"] += 1
                relative = _relative(path, self.kb)
                if not filename_re.fullmatch(path.name):
                    self.add(
                        "warning",
                        "REPORT_FILENAME_INVALID",
                        relative,
                        f"{kind} 报告文件名不符合当前规范。",
                    )
                text = path.read_text(encoding="utf-8")
                parts = re.split(
                    r"(?m)^## (?:引用|来源索引)\s*$", text, maxsplit=1
                )
                body_markers = set(REPORT_MARKER_RE.findall(parts[0]))
                citations = (
                    {
                        match.group(1)
                        for line in parts[1].splitlines()
                        if (match := REPORT_CITATION_RE.match(line))
                    }
                    if len(parts) == 2
                    else set()
                )
                missing = sorted(body_markers - citations)
                unused = sorted(citations - body_markers)
                if missing:
                    self.add(
                        "error",
                        "REPORT_CITATION_MISSING",
                        relative,
                        "正文引用没有对应条目: " + ", ".join(missing),
                    )
                if unused:
                    self.add(
                        "warning",
                        "REPORT_CITATION_UNUSED",
                        relative,
                        "引用条目未被正文使用: " + ", ".join(unused),
                    )

    def scan_index(self) -> None:
        knowledge_dir = self.kb / "knowledge"
        if not knowledge_dir.is_dir():
            return
        command = [
            sys.executable,
            str(self.skill_root / "bin/rebuild_index.py"),
            str(self.kb),
            "--dry-run",
        ]
        result = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode != 0:
            self.add(
                "error",
                "INDEX_REBUILD_FAILED",
                "INDEX.md",
                "无法按当前代码生成预期 INDEX: " + result.stderr.strip(),
            )
            return
        path = self.kb / "INDEX.md"
        if not path.is_file():
            self.add(
                "warning",
                "INDEX_MISSING",
                "INDEX.md",
                "派生索引缺失。",
                auto_fix="index",
            )
        elif path.read_text(encoding="utf-8") != result.stdout:
            self.add(
                "warning",
                "INDEX_OUT_OF_DATE",
                "INDEX.md",
                "INDEX 与当前节点/raw_data 的确定性重建结果不一致。",
                auto_fix="index",
            )


def scan(kb: Path, skill_root: Path) -> DoctorReport:
    return Doctor(kb, skill_root).scan()


def apply_repairs(
    kb: Path,
    skill_root: Path,
    actions: Iterable[str],
    *,
    autolink: bool = False,
    dry_run: bool = False,
) -> List[Dict[str, Any]]:
    requested = list(dict.fromkeys(actions))
    allowed = {"index", "links"}
    unknown = sorted(set(requested) - allowed)
    if unknown:
        raise ValueError("未知 repair action: " + ", ".join(unknown))
    results: List[Dict[str, Any]] = []
    for action in requested:
        if action == "index":
            command = [
                sys.executable,
                str(skill_root / "bin/rebuild_index.py"),
                str(kb),
            ]
            if dry_run:
                command.append("--dry-run")
        else:
            command = [
                sys.executable,
                str(skill_root / "bin/repair_links.py"),
                str(kb),
            ]
            if dry_run:
                command.append("--dry-run")
            if autolink:
                command.append("--autolink")
        result = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        acceptable = result.returncode in ({0} if action == "index" else {0, 3})
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        if action == "index" and dry_run and acceptable:
            # rebuild_index --dry-run 的 stdout 是完整 INDEX；doctor 只保留
            # stderr 中的计数摘要，避免 text/JSON 报告意外膨胀。
            stdout = ""
        results.append(
            {
                "action": action,
                "ok": acceptable,
                "exit_code": result.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "mode": "dry-run" if dry_run else "apply",
            }
        )
        if not acceptable:
            break
    return results


def render_text(report: DoctorReport) -> str:
    summary = report.summary()
    lines = [
        "byteworker · doctor",
        f"数据目录: {report.kb}",
        f"目标 profile: {report.schema_profile} ({report.schema_fingerprint})",
        (
            "扫描: nodes={nodes} raws={raws} provenance={provenance} "
            "reports={reports}"
        ).format(**report.counts),
        (
            "结果: error={error} warning={warning} info={info} "
            "auto_fixable={auto_fixable}"
        ).format(**summary),
    ]
    for severity in ("error", "warning", "info"):
        items = [item for item in report.findings if item.severity == severity]
        if not items:
            continue
        label = {"error": "错误", "warning": "警告", "info": "兼容信息"}[severity]
        lines.extend(["", f"## {label} ({len(items)})"])
        for item in items:
            suffix = f" [可自动修复:{item.auto_fix}]" if item.auto_fix else ""
            lines.append(
                f"- [{item.code}] {item.path}: {item.message}{suffix}"
            )
            if item.repair:
                lines.append(f"  建议: {item.repair}")
    if not report.findings:
        lines.extend(["", "✓ 知识库与当前 schema/profile 一致。"])
    elif summary["auto_fixable"]:
        lines.extend(
            [
                "",
                "可先运行: python3 bin/doctor.py fix",
                "修复后仍会重新扫描；语义/真相源问题不会自动改写。",
            ]
        )
    return "\n".join(lines) + "\n"


def render_json(report: DoctorReport) -> str:
    return json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n"
