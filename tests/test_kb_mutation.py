import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import kb_mutation  # noqa: E402
from kb_mutation import (  # noqa: E402
    MutationError,
    execute_mutation,
    validate_mutation,
)


def sha(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


class KbMutationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name)
        self.kb = self.root / "kb"
        self.work = self.root / "work"
        self.kb.mkdir()
        self.work.mkdir()
        (self.kb / "journal").mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.kb, check=True)
        subprocess.run(
            ["git", "config", "user.email", "mutation@example.test"],
            cwd=self.kb,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Mutation Test"],
            cwd=self.kb,
            check=True,
        )
        self.context = self.kb / "context.md"
        self.context.write_text(
            "# context\n\n"
            "## 我的身份\nold identity\n\n"
            "## 我的职责范围\nold scope\n\n"
            "## 我的当前重点\nold focus\n\n"
            "## 主管方向\n\n"
            "## 当前约束\n\n"
            "## 交互与提醒偏好\nold preference\n\n"
            "## 背景信息\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "."], cwd=self.kb, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=self.kb, check=True)

    def tearDown(self):
        self.temp.cleanup()

    def plan(self, write, *, operation="context"):
        value = {
            "schema_version": "byteworker-kb-mutation/v1",
            "operation": operation,
            "conflict_disposition": "no_conflict",
            "conflict_evidence": [],
            "writes": [write],
            "journal": {"action": operation, "summary": "test mutation"},
            "commit": {"message": f"{operation}: test mutation"},
        }
        path = self.work / "plan.json"
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def test_context_section_patch_is_atomic_and_preserves_other_sections(self):
        candidate = self.work / "section.md"
        candidate.write_text("- 2026-07-31 —— ship fix\n", encoding="utf-8")
        before = self.context.read_bytes()
        plan = self.plan(
            {
                "path": "context.md",
                "mode": "replace_section",
                "section": "我的当前重点",
                "content_path": str(candidate),
                "base_sha256": sha(before),
            }
        )

        receipt = execute_mutation(self.kb, plan, ROOT)

        rendered = self.context.read_text(encoding="utf-8")
        self.assertIn("- 2026-07-31 —— ship fix", rendered)
        self.assertIn("old identity", rendered)
        self.assertIn("old scope", rendered)
        self.assertEqual("committed", receipt["status"])
        self.assertFalse(receipt["index_rebuilt"])
        self.assertEqual(
            {"context.md", receipt["journal"]},
            set(
                subprocess.run(
                    ["git", "show", "--pretty=format:", "--name-only", "HEAD"],
                    cwd=self.kb,
                    text=True,
                    stdout=subprocess.PIPE,
                    check=True,
                ).stdout.splitlines()
            ),
        )

    def test_report_replacement_preserves_manual_section(self):
        report = self.kb / "reports/daily/2026-07-31.md"
        report.parent.mkdir(parents=True)
        report.write_text(
            "# daily\n\n## 本日重点\nold\n\n"
            "## 手动补充 / 备注\nkeep this\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "."], cwd=self.kb, check=True)
        subprocess.run(["git", "commit", "-qm", "report"], cwd=self.kb, check=True)
        candidate = self.work / "report.md"
        candidate.write_text(
            "# daily\n\n## 本日重点\nnew\n\n"
            "## 手动补充 / 备注\nreplace me\n",
            encoding="utf-8",
        )
        plan = self.plan(
            {
                "path": "reports/daily/2026-07-31.md",
                "mode": "replace_preserving_sections",
                "preserve_sections": ["手动补充 / 备注"],
                "content_path": str(candidate),
                "base_sha256": sha(report.read_bytes()),
            },
            operation="report",
        )

        execute_mutation(self.kb, plan, ROOT)

        rendered = report.read_text(encoding="utf-8")
        self.assertIn("## 本日重点\nnew", rendered)
        self.assertIn("## 手动补充 / 备注\nkeep this", rendered)
        self.assertNotIn("replace me", rendered)

    def test_legacy_reports_im_is_read_only(self):
        candidate = self.work / "legacy-im.md"
        candidate.write_text("# replacement\n", encoding="utf-8")
        plan = self.plan(
            {
                "path": "reports/im/2026-08-03.md",
                "mode": "replace",
                "content_path": str(candidate),
                "base_sha256": "",
            },
            operation="report",
        )

        with self.assertRaises(MutationError) as caught:
            execute_mutation(self.kb, plan, ROOT)

        self.assertEqual("KB_MUTATION_PATH_FORBIDDEN", caught.exception.code)
        self.assertFalse((self.kb / "reports/im/2026-08-03.md").exists())

    def test_stale_baseline_and_undeclared_knowledge_conflict_fail_closed(self):
        candidate = self.work / "candidate.md"
        candidate.write_text("new\n", encoding="utf-8")
        stale = self.plan(
            {
                "path": "context.md",
                "mode": "replace",
                "content_path": str(candidate),
                "base_sha256": "sha256:" + "0" * 64,
            }
        )
        with self.assertRaises(MutationError) as caught:
            validate_mutation(self.kb, stale, ROOT)
        self.assertEqual("KB_MUTATION_BASE_MISMATCH", caught.exception.code)

        node = self.kb / "knowledge/projects/project-x.md"
        node.parent.mkdir(parents=True)
        node.write_text("old\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.kb, check=True)
        subprocess.run(["git", "commit", "-qm", "node"], cwd=self.kb, check=True)
        plan_path = self.plan(
            {
                "path": "knowledge/projects/project-x.md",
                "mode": "replace",
                "content_path": str(candidate),
                "base_sha256": sha(node.read_bytes()),
            },
            operation="update",
        )
        value = json.loads(plan_path.read_text(encoding="utf-8"))
        del value["conflict_disposition"]
        plan_path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(MutationError) as caught:
            validate_mutation(self.kb, plan_path, ROOT)
        self.assertEqual(
            "KB_MUTATION_CONFLICT_UNDECLARED",
            caught.exception.code,
        )

    def test_operation_target_and_write_fields_are_strict(self):
        candidate = self.work / "candidate.md"
        candidate.write_text("new\n", encoding="utf-8")
        mismatched = self.plan(
            {
                "path": "context.md",
                "mode": "replace",
                "content_path": str(candidate),
                "base_sha256": sha(self.context.read_bytes()),
            },
            operation="report",
        )
        with self.assertRaises(MutationError) as caught:
            validate_mutation(self.kb, mismatched, ROOT)
        self.assertEqual("KB_MUTATION_PLAN_INVALID", caught.exception.code)

        value = json.loads(mismatched.read_text(encoding="utf-8"))
        value["operation"] = "context"
        value["writes"][0]["content"] = "ignored typo"
        mismatched.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(MutationError) as caught:
            validate_mutation(self.kb, mismatched, ROOT)
        self.assertEqual("KB_MUTATION_PLAN_INVALID", caught.exception.code)
        self.assertIn("未知字段", str(caught.exception))

    def test_commit_failure_restores_content_and_index(self):
        candidate = self.work / "candidate.md"
        candidate.write_text("replacement\n", encoding="utf-8")
        before = self.context.read_bytes()
        plan = self.plan(
            {
                "path": "context.md",
                "mode": "replace",
                "content_path": str(candidate),
                "base_sha256": sha(before),
            }
        )
        real_git = kb_mutation._git

        def fail_commit(kb, args, *, check=True):
            if args[:1] == ["commit"]:
                raise MutationError("KB_MUTATION_GIT_ERROR", "forced commit failure")
            return real_git(kb, args, check=check)

        with mock.patch.object(kb_mutation, "_git", side_effect=fail_commit):
            with self.assertRaises(MutationError):
                execute_mutation(self.kb, plan, ROOT)

        self.assertEqual(before, self.context.read_bytes())
        self.assertEqual(
            "",
            subprocess.run(
                ["git", "status", "--porcelain=v1"],
                cwd=self.kb,
                text=True,
                stdout=subprocess.PIPE,
                check=True,
            ).stdout,
        )


if __name__ == "__main__":
    unittest.main()
