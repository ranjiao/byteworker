import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
BIN = ROOT / "bin"
for path in (BIN, LIB):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from digest_txn import DigestTxnError, _validate_node_candidate  # noqa: E402
from rebuild_index import render_node_section  # noqa: E402


class ResolveUsersTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name)
        self.mock_bin = self.root / "bin"
        self.mock_bin.mkdir()
        lark_cli = self.mock_bin / "lark-cli"
        lark_cli.write_text(
            """#!/usr/bin/env bash
if [[ " $* " == *" +search-user "* ]]; then
  if [[ " $* " == *" ou_external "* ]]; then
    printf '%s\\n' '{
      "data": {
        "users": [{
          "open_id": "ou_external",
          "localized_name": "外部联系人",
          "email": "external@example.net",
          "enterprise_email": "",
          "department": "",
          "is_activated": true,
          "is_cross_tenant": true
        }]
      }
    }'
    exit 0
  fi
  printf '%s\\n' '{
    "data": {
      "users": [{
        "open_id": "ou_alpha",
        "localized_name": "张三",
        "email": "",
        "enterprise_email": "zhangsan@example.com",
        "department": "Data-示例团队",
        "is_activated": true,
        "is_cross_tenant": false
      }]
    }
  }'
  exit 0
fi
printf '%s\\n' '{"data":{"user":{}}}'
""",
            encoding="utf-8",
        )
        lark_cli.chmod(0o755)
        self.env = os.environ.copy()
        self.env["PATH"] = str(self.mock_bin) + os.pathsep + self.env["PATH"]

    def tearDown(self):
        self.temp.cleanup()

    def run_resolver(self, *args):
        return subprocess.run(
            ["bash", str(ROOT / "bin/resolve-users.sh"), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self.env,
            check=False,
        )

    def test_default_tsv_keeps_legacy_three_column_contract(self):
        result = self.run_resolver("--ids", "ou_alpha")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("ou_alpha\t张三\tzhangsan\n", result.stdout)
        self.assertIn("resolved=1/1", result.stderr)

    def test_json_includes_directory_profile_and_verification_time(self):
        result = self.run_resolver("--ids", "ou_alpha", "--format", "json")

        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(
            "byteworker-resolved-users/v1",
            payload["schema_version"],
        )
        self.assertRegex(
            payload["resolved_at"],
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$",
        )
        self.assertEqual(
            {
                "open_id": "ou_alpha",
                "name": "张三",
                "feishu_id": "zhangsan",
                "email": "zhangsan@example.com",
                "enterprise_email": "zhangsan@example.com",
                "department_path": "Data-示例团队",
                "is_activated": True,
                "is_cross_tenant": False,
            },
            payload["users"][0],
        )

    def test_unknown_format_fails_closed(self):
        result = self.run_resolver(
            "--ids",
            "ou_alpha",
            "--format",
            "yaml",
        )

        self.assertEqual(1, result.returncode)
        self.assertIn("--format 仅支持 tsv 或 json", result.stderr)

    def test_personal_email_is_not_promoted_to_feishu_id(self):
        result = self.run_resolver(
            "--ids",
            "ou_external",
            "--format",
            "json",
        )

        self.assertEqual(0, result.returncode, result.stderr)
        user = json.loads(result.stdout)["users"][0]
        self.assertEqual("external@example.net", user["email"])
        self.assertEqual("", user["enterprise_email"])
        self.assertEqual("?", user["feishu_id"])
        self.assertTrue(user["is_cross_tenant"])


class PersonDirectoryCandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name)
        self.kb = self.root / "kb"
        (self.kb / "knowledge/people").mkdir(parents=True)
        self.kb = self.kb.resolve()
        self.candidate = self.root / "person-candidate.md"
        self.manifest = self.root / "plan.json"
        self.manifest.write_text("{}\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def candidate_text(
        self,
        directory_verified_at=True,
        enterprise_email="zhangsan@example.com",
        feishu_id="zhangsan",
    ):
        timestamp = (
            "directory_verified_at: 2026-07-30T17:20:00+08:00\n"
            if directory_verified_at
            else ""
        )
        return f"""---
id: person-zhang-san
title: 张三
type: person
feishu_id: {feishu_id}
enterprise_email: {enterprise_email}
department_path: Data-示例团队
{timestamp}tags: []
status: current
created: 2026-07-30
updated: 2026-07-30
last_verified: 2026-07-30
sources:
  - https://example.test/source
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
"""

    def validate(self):
        return _validate_node_candidate(
            self.kb,
            {
                "op": "create",
                "path": "knowledge/people/person-zhang-san.md",
                "candidate": str(self.candidate),
            },
            self.manifest,
            [],
            {},
        )

    def test_person_candidate_requires_directory_verification_timestamp(self):
        self.candidate.write_text(
            self.candidate_text(directory_verified_at=False),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            DigestTxnError,
            "directory_verified_at",
        ):
            self.validate()

    def test_person_candidate_with_directory_profile_is_valid(self):
        self.candidate.write_text(
            self.candidate_text(),
            encoding="utf-8",
        )

        result = self.validate()

        self.assertEqual("person-zhang-san", result["id"])
        self.assertEqual("Data-示例团队", result["frontmatter"]["department_path"])

    def test_person_candidate_rejects_invalid_identity_fields(self):
        self.candidate.write_text(
            self.candidate_text(feishu_id="?"),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(DigestTxnError, "feishu_id"):
            self.validate()

        self.candidate.write_text(
            self.candidate_text(enterprise_email="invalid-email"),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(DigestTxnError, "enterprise_email"):
            self.validate()

    def test_person_index_exposes_current_department_path(self):
        self.candidate.write_text(self.candidate_text(), encoding="utf-8")
        target = self.kb / "knowledge/people/person-zhang-san.md"
        target.write_text(self.candidate.read_text(encoding="utf-8"), encoding="utf-8")

        lines, count, malformed = render_node_section(
            str(self.kb),
            "people",
            "person",
            "人员",
        )
        rendered = "\n".join(lines)

        self.assertEqual(1, count)
        self.assertEqual([], malformed)
        self.assertIn("| id | 标题 | feishu_id | department_path |", rendered)
        self.assertIn("| person-zhang-san | 张三 | zhangsan | Data-示例团队 |", rendered)


class PersonDirectoryArchitectureContractTests(unittest.TestCase):
    def test_architecture_and_agent_rules_name_versioned_directory_contract(self):
        architecture = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        digest_core = (ROOT / "references/digest-core.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("byteworker-resolved-users/v1", architecture)
        self.assertIn("默认三列 TSV", architecture)
        self.assertIn("bin/resolve-users.sh --format json", skill)
        self.assertIn("department_path", digest_core)
        self.assertIn("查询为空不清除旧值", digest_core)


if __name__ == "__main__":
    unittest.main()
