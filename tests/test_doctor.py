import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from doctor import SCHEMA_CONTRACT_PATHS, apply_repairs, scan  # noqa: E402
from source_profiles import profile_relative_path, validate_profile  # noqa: E402
import update_postflight  # noqa: E402
from update_postflight import render_message, run_postflight  # noqa: E402


NODE_DIRS = (
    "people",
    "projects",
    "areas",
    "orgs",
    "events",
    "decisions",
    "readings",
    "thinkings",
)


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.kb = Path(self.temp.name)
        for directory in NODE_DIRS:
            (self.kb / "knowledge" / directory).mkdir(parents=True)
        for directory in ("sources", "raw_data", "provenance", "journal"):
            (self.kb / directory).mkdir()
        for directory in ("daily", "weekly", "im"):
            (self.kb / "reports" / directory).mkdir(parents=True)
        for name in ("context.md", "todo.md", "dashboard.md"):
            (self.kb / name).write_text(f"# {name}\n", encoding="utf-8")
        self.git("init")
        self.git("config", "user.email", "doctor@example.test")
        self.git("config", "user.name", "Doctor Tests")
        self.write_fixture()
        self.rebuild_index()

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        subprocess.run(
            ["git", "-C", str(self.kb), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def rebuild_index(self):
        subprocess.run(
            [sys.executable, str(ROOT / "bin/rebuild_index.py"), str(self.kb)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def commit_all(self, message="fixture"):
        self.git("add", ".")
        self.git("commit", "-m", message)

    def add_one_way_area_link(self):
        (self.kb / "knowledge/areas/area-example.md").write_text(
            """---
id: area-example
title: 示例领域
type: area
tags: []
status: current
created: 2026-07-28
updated: 2026-07-28
last_verified: 2026-07-28
sources:
  - https://example.test/area
links: []
---

# 示例领域

> **TL;DR:** 示例领域。

## 概述 / 定义
## 关键知识点
## 规范 / 流程 / how-to
## 踩坑 / 注意事项
## 思路与视角
## 相关节点与外部链接
""",
            encoding="utf-8",
        )
        reading = self.kb / "knowledge/readings/reading-example.md"
        reading.write_text(
            reading.read_text(encoding="utf-8").replace(
                "links: []", "links: [area-example]"
            ),
            encoding="utf-8",
        )
        self.rebuild_index()

    def write_fixture(self):
        raw_id = "raw-2026-07-28-example"
        (self.kb / "raw_data/2026-07-28-example.md").write_text(
            f"""---
raw_id: {raw_id}
ingested: 2026-07-28T10:00:00+08:00
source_type: web
source_uid: https://example.test/source
payload_schema: byteworker-payload-v1
payload_components:
  - body|body|sha256:{'a' * 64}
content_hash: sha256:{'b' * 64}
digest_key: web:https://example.test/source:-:sha256:{'b' * 64}
source_url: https://example.test/source
source_title: 示例
digest_status: digested
digest_targets:
  - reading-example
---

raw body
""",
            encoding="utf-8",
        )
        (self.kb / "provenance" / f"{raw_id}.json").write_text(
            json.dumps(
                {
                    "schema_version": "byteworker-provenance/v1",
                    "raw_id": raw_id,
                    "raw_path": "raw_data/2026-07-28-example.md",
                    "derived_from": {"content_hash": "sha256:" + "b" * 64},
                    "anchors": [
                        {
                            "anchor_id": "source",
                            "raw_id": raw_id,
                            "kind": "source",
                            "precision": "source_only",
                            "locator": {"source_uid": "https://example.test/source"},
                            "open_url": "https://example.test/source",
                        }
                    ],
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        (self.kb / "knowledge/readings/reading-example.md").write_text(
            f"""---
id: reading-example
title: 示例资料
type: reading
tags: [example]
status: current
created: 2026-07-28
updated: 2026-07-28
last_verified: 2026-07-28
sources:
  - {raw_id}
primary_source: {raw_id}
primary_source_url: https://example.test/source
links: []
---

# 示例资料

> **TL;DR:** 示例摘要。

## 来源

- [原文](https://example.test/source)

## 核心观点

- 示例事实。[E1]

## 可借鉴点

## 相关节点

## 证据

| 编号 | 原始来源 | 定位 | 原文时间 | 收录时间 | 精度 |
|---|---|---|---|---|---|
| **[E1]** | [示例][E1] · `{raw_id}` | 来源级 · anchor_id=source | 未记录 | 2026-07-28T10:00:00+08:00 | source_only |

[E1]: <https://example.test/source>
""",
            encoding="utf-8",
        )

    def codes(self):
        return [item.code for item in scan(self.kb, ROOT).findings]

    def test_evidence_table_allows_escaped_pipe_in_source_title(self):
        reading = self.kb / "knowledge/readings/reading-example.md"
        reading.write_text(
            reading.read_text(encoding="utf-8").replace(
                "[示例][E1]", r"[08-03 \| 架构工作同步][E1]"
            ),
            encoding="utf-8",
        )
        findings = [
            item.code
            for item in scan(self.kb, ROOT).findings
            if item.path.endswith("reading-example.md")
        ]
        self.assertNotIn("NODE_EVIDENCE_ROW_INCOMPLETE", findings)
        self.assertNotIn("NODE_EVIDENCE_MARKER_MISMATCH", findings)

    def test_minimal_effective_thinking_is_valid(self):
        (self.kb / "knowledge/thinkings/thinking-ai-cognition.md").write_text(
            """---
id: thinking-ai-cognition
title: 我对 AI 的认知
type: thinking
status: effective
created: 2026-08-04
updated: 2026-08-04
---

# 我对 AI 的认知

认知会持续更新，不要求固定章节或外部来源。
""",
            encoding="utf-8",
        )
        report = scan(self.kb, ROOT)
        thinking_findings = [
            item
            for item in report.findings
            if item.path.endswith("thinking-ai-cognition.md")
        ]
        self.assertEqual([], thinking_findings)

    def test_thinking_rejects_non_binary_status(self):
        path = self.kb / "knowledge/thinkings/thinking-ai-cognition.md"
        path.write_text(
            """---
id: thinking-ai-cognition
title: 我对 AI 的认知
type: thinking
status: exploring
created: 2026-08-04
updated: 2026-08-04
---

# 我对 AI 的认知

仍在形成。
""",
            encoding="utf-8",
        )
        findings = [
            item
            for item in scan(self.kb, ROOT).findings
            if item.path.endswith("thinking-ai-cognition.md")
        ]
        self.assertIn("NODE_INVALID_STATUS", [item.code for item in findings])

    def test_person_directory_fields_require_valid_verification_metadata(self):
        person = self.kb / "knowledge/people/person-zhang-san.md"
        person.write_text(
            """---
id: person-zhang-san
title: 张三
type: person
feishu_id: zhangsan
enterprise_email: invalid-email
department_path: Data-示例团队
tags: []
status: current
created: 2026-07-30
updated: 2026-07-30
last_verified: 2026-07-30
sources:
  - https://example.test/person
links: []
---

# 张三

> **TL;DR:** 示例人员。

## 基本信息
## 负责什么
## 协作历史与关键交互
## 立场 / 利益 / 动机
## 偏好 / 风格 / 注意点
## 关联节点
""",
            encoding="utf-8",
        )

        codes = self.codes()

        self.assertIn("PERSON_DIRECTORY_UNVERIFIED", codes)
        self.assertIn("PERSON_ENTERPRISE_EMAIL_INVALID", codes)

        text = person.read_text(encoding="utf-8")
        text = text.replace(
            "enterprise_email: invalid-email\n",
            "enterprise_email: zhangsan@example.com\n"
            "directory_verified_at: 2026-07-30T17:20:00+08:00\n",
        )
        person.write_text(text, encoding="utf-8")
        codes = self.codes()
        self.assertNotIn("PERSON_DIRECTORY_UNVERIFIED", codes)
        self.assertNotIn("PERSON_ENTERPRISE_EMAIL_INVALID", codes)

        text = person.read_text(encoding="utf-8").replace(
            "directory_verified_at: 2026-07-30T17:20:00+08:00",
            "directory_verified_at: 2026-07-30",
        )
        person.write_text(text, encoding="utf-8")
        self.assertIn("PERSON_DIRECTORY_TIMESTAMP_INVALID", self.codes())

    def write_routine_raw(
        self,
        *,
        slug: str,
        source_type: str,
        source_uid: str = "",
    ):
        uid = f"source_uid: {source_uid}\n" if source_uid else ""
        path = self.kb / "raw_data" / f"2026-07-29-{slug}.md"
        path.write_text(
            f"""---
raw_id: raw-2026-07-29-{slug}
ingested: 2026-07-29T10:00:00+08:00
source_type: {source_type}
{uid}source_url: https://example.test/{slug}
source_title: {slug}
routine: weekly
digest_status: digested
digest_targets: []
---

legacy routine body
""",
            encoding="utf-8",
        )
        return path

    def write_feishu_profile(self, *, source_uid: str, enabled: bool = False):
        value = validate_profile(
            {
                "schema_version": "byteworker-source-profile/v2",
                "source_type": "feishu_doc",
                "source_uid": source_uid,
                "source_url": f"https://bytedance.larkoffice.com/docx/{source_uid}",
                "title": "滚动文档",
                "selector": {"document_id": source_uid},
                "capture_policy": {
                    "comments": True,
                    "whiteboards": True,
                },
                "routine": {
                    "enabled": enabled,
                    "cadence": "weekly" if enabled else None,
                },
            }
        )
        relative = profile_relative_path(value)
        path = self.kb / relative
        path.write_text(
            json.dumps(value, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return relative

    def test_healthy_current_schema_fixture(self):
        report = scan(self.kb, ROOT)
        self.assertEqual([], [item for item in report.findings if item.severity != "info"])
        self.assertEqual(1, report.counts["nodes"])
        self.assertEqual(1, report.counts["raws"])
        self.assertEqual(1, report.counts["provenance"])
        self.assertEqual(0, report.counts["profiles"])
        self.assertEqual(0, report.counts["routine_sources"])

    def test_sources_directory_is_part_of_current_layout(self):
        (self.kb / "sources").rmdir()
        findings = scan(self.kb, ROOT).findings
        self.assertTrue(
            any(
                item.code == "LAYOUT_MISSING_DIRECTORY"
                and item.path == "sources"
                for item in findings
            )
        )

    def test_invalid_profile_is_reported_without_aborting_scan(self):
        (self.kb / "sources/bad.json").write_text("{}\n", encoding="utf-8")
        report = scan(self.kb, ROOT)
        self.assertIn("SOURCE_PROFILE_INVALID", [item.code for item in report.findings])
        self.assertEqual(0, report.counts["profiles"])
        self.assertEqual(1, report.counts["nodes"])

    def test_routine_profile_coverage_uses_compatibility_severity(self):
        self.write_routine_raw(
            slug="rolling-doc",
            source_type="feishu_doc",
            source_uid="docx123",
        )
        self.write_routine_raw(
            slug="meego-view",
            source_type="meego",
            source_uid="meego:project:view",
        )
        self.write_routine_raw(
            slug="chat",
            source_type="feishu_chat",
            source_uid="oc_chat",
        )

        report = scan(self.kb, ROOT)
        findings = {item.code: item for item in report.findings}
        self.assertEqual(
            "warning",
            findings["ROUTINE_SOURCE_PROFILE_MISSING"].severity,
        )
        self.assertEqual(
            "error",
            findings["ROUTINE_SOURCE_PROFILE_REQUIRED"].severity,
        )
        self.assertNotIn("ROUTINE_SOURCE_PROFILE_UNSUPPORTED", findings)
        self.assertEqual(
            2,
            sum(
                item.code == "ROUTINE_SOURCE_PROFILE_REQUIRED"
                for item in report.findings
            ),
        )
        self.assertEqual(3, report.counts["routine_sources"])
        self.assertEqual(3, report.counts["legacy_routine_sources"])

    def test_disabled_profile_suppresses_legacy_raw_routine(self):
        self.write_routine_raw(
            slug="rolling-doc",
            source_type="feishu_doc",
            source_uid="docx123",
        )
        self.write_feishu_profile(source_uid="docx123", enabled=False)

        report = scan(self.kb, ROOT)
        codes = [item.code for item in report.findings]
        self.assertNotIn("ROUTINE_SOURCE_PROFILE_MISSING", codes)
        self.assertEqual(1, report.counts["profiles"])
        self.assertEqual(0, report.counts["routine_sources"])
        self.assertEqual(0, report.counts["legacy_routine_sources"])

    def test_routine_without_stable_uid_is_reported(self):
        self.write_routine_raw(
            slug="legacy-doc",
            source_type="feishu_doc",
        )
        self.assertIn("ROUTINE_SOURCE_UID_MISSING", self.codes())

    def test_raw_profile_binding_identity_is_checked(self):
        relative = self.write_feishu_profile(
            source_uid="docx123",
            enabled=False,
        )
        raw = self.kb / "raw_data/2026-07-28-example.md"
        text = raw.read_text(encoding="utf-8").replace(
            "payload_schema: byteworker-payload-v1\n",
            (
                f"source_profile_path: {relative}\n"
                f"source_profile_revision: sha256:{'c' * 64}\n"
                "payload_schema: byteworker-payload-v1\n"
            ),
        )
        raw.write_text(text, encoding="utf-8")
        self.assertIn("RAW_PROFILE_IDENTITY_MISMATCH", self.codes())

    def test_current_raw_component_and_digest_key_contracts_are_checked(self):
        raw = self.kb / "raw_data/2026-07-28-example.md"
        text = raw.read_text(encoding="utf-8")
        text = text.replace(
            f"  - body|body|sha256:{'a' * 64}",
            "  - malformed",
        )
        text = re.sub(
            r"(?m)^digest_key:.*$",
            "digest_key: wrong",
            text,
        )
        raw.write_text(text, encoding="utf-8")
        codes = self.codes()
        self.assertIn("RAW_PAYLOAD_COMPONENT_INVALID", codes)
        self.assertIn("RAW_DIGEST_KEY_MISMATCH", codes)

    def test_record_index_contract_is_checked(self):
        raw = self.kb / "raw_data/2026-07-28-example.md"
        record = {
            "record_id": "duplicate",
            "title": "记录",
            "locator": {"id": "duplicate"},
            "fields": {},
        }
        document = {
            "schema_version": "byteworker-record-index/v1",
            "source_type": "web",
            "source_uid": "https://example.test/source",
            "records": [record, record],
        }
        raw.write_text(
            raw.read_text(encoding="utf-8")
            + "\n## 规范记录索引（派生）\n\n```json\n"
            + json.dumps(document, ensure_ascii=False)
            + "\n```\n",
            encoding="utf-8",
        )
        self.assertIn("RAW_RECORD_INDEX_DUPLICATE_ID", self.codes())

    def test_schema_fingerprint_covers_refactored_persistence_contracts(self):
        for relative in (
            "lib/source_profiles.py",
            "lib/sources/models.py",
            "lib/sources/transaction_bridge.py",
            "lib/digest_txn.py",
            "lib/provenance.py",
        ):
            self.assertIn(relative, SCHEMA_CONTRACT_PATHS)

    def test_fix_rebuilds_index_and_repairs_links(self):
        (self.kb / "knowledge/areas/area-example.md").write_text(
            """---
id: area-example
title: 示例领域
type: area
tags: []
status: current
created: 2026-07-28
updated: 2026-07-28
last_verified: 2026-07-28
sources:
  - https://example.test/area
links: []
---

# 示例领域

> **TL;DR:** 示例领域。

## 概述 / 定义
## 关键知识点
## 规范 / 流程 / how-to
## 踩坑 / 注意事项
## 思路与视角
## 相关节点与外部链接
""",
            encoding="utf-8",
        )
        reading = self.kb / "knowledge/readings/reading-example.md"
        text = reading.read_text(encoding="utf-8").replace(
            "links: []", "links: [area-example, area-example]"
        )
        reading.write_text(text, encoding="utf-8")

        before = self.codes()
        self.assertIn("INDEX_OUT_OF_DATE", before)
        self.assertIn("NODE_BACKLINK_MISSING", before)
        self.assertIn("NODE_LINK_DUPLICATE", before)

        result = apply_repairs(self.kb, ROOT, ["index", "links"])
        self.assertTrue(all(item["ok"] for item in result))
        after = self.codes()
        self.assertNotIn("INDEX_OUT_OF_DATE", after)
        self.assertNotIn("NODE_BACKLINK_MISSING", after)
        self.assertNotIn("NODE_LINK_DUPLICATE", after)

    def test_fix_does_not_guess_dangling_or_missing_sections(self):
        reading = self.kb / "knowledge/readings/reading-example.md"
        text = reading.read_text(encoding="utf-8")
        text = text.replace("links: []", "links: [project-missing]")
        text = text.replace("## 可借鉴点\n", "")
        reading.write_text(text, encoding="utf-8")
        apply_repairs(self.kb, ROOT, ["index", "links"])
        after = self.codes()
        self.assertIn("NODE_DANGLING_LINK", after)
        self.assertIn("NODE_MISSING_SECTIONS", after)

    def test_missing_provenance_is_reported_without_aborting_scan(self):
        (self.kb / "provenance/raw-2026-07-28-example.json").unlink()
        codes = self.codes()
        self.assertIn("RAW_CURRENT_SCHEMA_MISSING_PROVENANCE", codes)
        self.assertIn("NODE_EVIDENCE_PROVENANCE_MISSING", codes)

    def test_cli_json_is_machine_readable(self):
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "bin/doctor.py"),
                "scan",
                "--kb",
                str(self.kb),
                "--format",
                "json",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual("byteworker-kb/v1", payload["schema_profile"])
        self.assertEqual(1, payload["counts"]["nodes"])

    def test_cli_fix_uses_transaction_and_creates_local_commit(self):
        self.commit_all()
        (self.kb / "INDEX.md").write_text("# stale\n", encoding="utf-8")
        self.git("add", "INDEX.md")
        self.git("commit", "-m", "make index stale")
        before = subprocess.run(
            ["git", "-C", str(self.kb), "rev-parse", "HEAD"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()

        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "bin/doctor.py"),
                "fix",
                "--kb",
                str(self.kb),
                "--only",
                "index",
                "--format",
                "json",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["transaction"]["commit"])
        self.assertEqual("healthy", payload["transaction"]["status"])
        after = subprocess.run(
            ["git", "-C", str(self.kb), "rev-parse", "HEAD"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        self.assertNotEqual(before, after)
        status = subprocess.run(
            ["git", "-C", str(self.kb), "status", "--short"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout
        self.assertEqual("", status)

    def test_index_fix_dry_run_does_not_embed_the_generated_index(self):
        result = apply_repairs(self.kb, ROOT, ["index"], dry_run=True)
        self.assertTrue(result[0]["ok"])
        self.assertEqual("", result[0]["stdout"])
        self.assertIn("INDEX 重建", result[0]["stderr"])

    def test_skill_and_help_route_users_to_doctor_reference(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        help_text = (ROOT / "references/help.md").read_text(encoding="utf-8")
        self.assertIn("references/doctor.md", skill)
        self.assertIn("bin/doctor.py", skill)
        self.assertIn("doctor", help_text)

    def test_legacy_raw_without_id_is_compatibly_identified_from_filename(self):
        path = self.kb / "raw_data/2026-07-28-example.md"
        text = path.read_text(encoding="utf-8")
        text = text.replace("raw_id: raw-2026-07-28-example\n", "")
        text = text.replace("payload_schema: byteworker-payload-v1\n", "")
        text = text.replace(
            "payload_components:\n"
            f"  - body|body|sha256:{'a' * 64}\n",
            "",
        )
        text = re.sub(r"(?m)^digest_key:.*\n", "", text)
        path.write_text(text, encoding="utf-8")
        report = scan(self.kb, ROOT)
        codes = [item.code for item in report.findings]
        self.assertIn("RAW_LEGACY_MISSING_ID", codes)
        self.assertIn("RAW_LEGACY_COMPATIBLE", codes)
        self.assertNotIn("NODE_SOURCE_MISSING_RAW", codes)

    def test_bold_report_citations_and_report_digest_targets_are_supported(self):
        report = self.kb / "reports/daily/2026-07-28.md"
        report.write_text(
            """# 日报 · 2026-07-28

## 本日重点

- 示例事实。[S1]

## 来源索引

- **[S1]** 示例来源。
""",
            encoding="utf-8",
        )
        raw = self.kb / "raw_data/2026-07-28-example.md"
        raw.write_text(
            raw.read_text(encoding="utf-8").replace(
                "  - reading-example",
                "  - reports/daily/2026-07-28.md",
            ),
            encoding="utf-8",
        )
        codes = [item.code for item in scan(self.kb, ROOT).findings]
        self.assertNotIn("REPORT_CITATION_MISSING", codes)
        self.assertNotIn("RAW_TARGET_MISSING_NODE", codes)
        self.assertNotIn("RAW_TARGET_MISSING_REPORT", codes)

    def test_postflight_repairs_commits_and_preserves_unrelated_dirty_file(self):
        self.add_one_way_area_link()
        self.commit_all()
        (self.kb / ".DS_Store").write_text("unrelated", encoding="utf-8")

        result = run_postflight(ROOT, self.kb)

        self.assertEqual("healthy", result.status)
        self.assertEqual(1, result.repaired_findings)
        self.assertEqual(1, result.changed_files)
        self.assertTrue(result.commit)
        area = (self.kb / "knowledge/areas/area-example.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("reading-example", area)
        self.assertEqual(
            ["?? .DS_Store"],
            subprocess.run(
                ["git", "-C", str(self.kb), "status", "--short"],
                text=True,
                stdout=subprocess.PIPE,
                check=True,
            ).stdout.splitlines(),
        )
        self.assertIn("兼容检查通过", render_message(result))

    def test_postflight_rebuilds_stale_index(self):
        self.commit_all()
        (self.kb / "INDEX.md").write_text("stale\n", encoding="utf-8")
        self.commit_all("stale index")

        result = run_postflight(ROOT, self.kb)

        self.assertEqual("healthy", result.status)
        self.assertEqual(1, result.repaired_findings)
        self.assertEqual(1, result.changed_files)
        self.assertNotEqual(
            "stale\n", (self.kb / "INDEX.md").read_text(encoding="utf-8")
        )

    def test_postflight_commit_failure_restores_files_index_and_head(self):
        self.add_one_way_area_link()
        self.commit_all()
        before_head = subprocess.run(
            ["git", "-C", str(self.kb), "rev-parse", "HEAD"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        before_status = subprocess.run(
            ["git", "-C", str(self.kb), "status", "--porcelain=v1"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout
        area = self.kb / "knowledge/areas/area-example.md"
        before_area = area.read_bytes()
        before_index = (self.kb / "INDEX.md").read_bytes()

        real_git = update_postflight._git

        def fail_commit(kb, *args):
            if args[:1] == ("commit",):
                return subprocess.CompletedProcess(
                    ["git", *args],
                    1,
                    stdout="",
                    stderr="forced commit failure",
                )
            return real_git(kb, *args)

        with mock.patch.object(
            update_postflight,
            "_git",
            side_effect=fail_commit,
        ):
            result = run_postflight(ROOT, self.kb)

        self.assertEqual("decision", result.status)
        self.assertIn("forced commit failure", result.reasons)
        self.assertEqual("", result.commit)
        self.assertEqual(
            before_head,
            subprocess.run(
                ["git", "-C", str(self.kb), "rev-parse", "HEAD"],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip(),
        )
        self.assertEqual(
            before_status,
            subprocess.run(
                ["git", "-C", str(self.kb), "status", "--porcelain=v1"],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout,
        )
        self.assertEqual(before_area, area.read_bytes())
        self.assertEqual(before_index, (self.kb / "INDEX.md").read_bytes())

    def test_postflight_auto_repairs_self_link_error(self):
        reading = self.kb / "knowledge/readings/reading-example.md"
        reading.write_text(
            reading.read_text(encoding="utf-8").replace(
                "links: []", "links: [reading-example]"
            ),
            encoding="utf-8",
        )
        self.commit_all()

        before = scan(self.kb, ROOT)
        finding = next(
            item for item in before.findings if item.code == "NODE_SELF_LINK"
        )
        self.assertEqual("error", finding.severity)
        self.assertEqual("links", finding.auto_fix)

        result = run_postflight(ROOT, self.kb)

        self.assertEqual("healthy", result.status)
        self.assertEqual(1, result.repaired_findings)
        self.assertEqual(0, result.summary["error"])
        self.assertNotIn(
            "reading-example",
            next(
                line
                for line in reading.read_text(encoding="utf-8").splitlines()
                if line.startswith("links:")
            ),
        )

    def test_postflight_reports_warning_without_writing(self):
        reading = self.kb / "knowledge/readings/reading-example.md"
        reading.write_text(
            reading.read_text(encoding="utf-8").replace("## 可借鉴点\n", ""),
            encoding="utf-8",
        )
        self.commit_all()

        result = run_postflight(ROOT, self.kb)

        self.assertEqual("notice", result.status)
        self.assertEqual(0, result.repaired_findings)
        self.assertEqual("", result.commit)
        self.assertIn("可忽略", render_message(result))

    def test_postflight_escalates_unhandled_error(self):
        reading = self.kb / "knowledge/readings/reading-example.md"
        reading.write_text(
            reading.read_text(encoding="utf-8").replace(
                "sources:\n  - raw-2026-07-28-example\n", ""
            ),
            encoding="utf-8",
        )
        self.commit_all()

        result = run_postflight(ROOT, self.kb)

        self.assertEqual("decision", result.status)
        self.assertGreater(result.summary["error"], 0)
        self.assertEqual("", result.commit)
        message = render_message(result)
        self.assertIn("请决定是否立即检查", message)
        self.assertNotIn("\n", message)

    def test_postflight_repairs_safe_findings_before_escalating_error(self):
        self.add_one_way_area_link()
        reading = self.kb / "knowledge/readings/reading-example.md"
        reading.write_text(
            reading.read_text(encoding="utf-8").replace(
                "sources:\n  - raw-2026-07-28-example\n", ""
            ),
            encoding="utf-8",
        )
        self.commit_all()

        result = run_postflight(ROOT, self.kb)

        self.assertEqual("decision", result.status)
        self.assertEqual(1, result.repaired_findings)
        self.assertTrue(result.commit)
        area = (self.kb / "knowledge/areas/area-example.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("reading-example", area)
        self.assertGreater(result.summary["error"], 0)

    def test_postflight_does_not_repair_over_active_node_edits(self):
        self.add_one_way_area_link()
        self.commit_all()
        reading = self.kb / "knowledge/readings/reading-example.md"
        reading.write_text(
            reading.read_text(encoding="utf-8") + "\nactive edit\n",
            encoding="utf-8",
        )

        result = run_postflight(ROOT, self.kb)

        self.assertEqual("decision", result.status)
        self.assertIn("knowledge 节点存在未提交编辑", result.reasons)
        area = (self.kb / "knowledge/areas/area-example.md").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("reading-example", area)

    def test_postflight_blocks_graph_repair_when_node_ids_are_duplicated(self):
        self.add_one_way_area_link()
        original = self.kb / "knowledge/areas/area-example.md"
        (self.kb / "knowledge/areas/area-zcopy.md").write_text(
            original.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        self.rebuild_index()
        self.commit_all()

        result = run_postflight(ROOT, self.kb)

        self.assertEqual("decision", result.status)
        self.assertIn(
            "节点图存在影响确定性修复的结构错误",
            result.reasons,
        )
        self.assertNotIn(
            "reading-example",
            original.read_text(encoding="utf-8"),
        )

    def test_postflight_blocks_repairs_when_profile_is_invalid(self):
        self.add_one_way_area_link()
        (self.kb / "sources/bad.json").write_text("{}\n", encoding="utf-8")
        self.commit_all()

        result = run_postflight(ROOT, self.kb)

        self.assertEqual("decision", result.status)
        self.assertIn(
            "知识库布局、Git 隔离或 source Profile 存在严重错误",
            result.reasons,
        )
        area = self.kb / "knowledge/areas/area-example.md"
        self.assertNotIn(
            "reading-example",
            area.read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
