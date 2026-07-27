import json
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from frontmatter import parse_file  # noqa: E402
from provenance_backfill import apply_backfill, audit_kb, build_plan  # noqa: E402


def git(kb: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(kb), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


class ProvenanceBackfillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name)
        self.kb = self.root / "kb"
        (self.kb / "raw_data").mkdir(parents=True)
        (self.kb / "knowledge/projects").mkdir(parents=True)
        (self.kb / "journal").mkdir()
        self.raw = self.kb / "raw_data/2026-07-20-project.md"
        self.raw.write_text(
            """---
raw_id: raw-2026-07-20-project
ingested: 2026-07-20T12:00:00+08:00
source_type: feishu_doc
source_uid: doc-project
source_revision: "3"
source_url: https://example.test/docx/project
source_title: 项目文档
content_hash: sha256:abc
digest_status: digested
digest_targets:
  - project-example
---

<heading id="doxcnBlock1">结论</heading>
""",
            encoding="utf-8",
        )
        self.node = self.kb / "knowledge/projects/project-example.md"
        self.node.write_text(
            """---
id: project-example
title: 示例项目
type: project
tags: [test]
status: current
created: 2026-07-20
updated: 2026-07-20
last_verified: 2026-07-20
sources:
  - raw-2026-07-20-project
links: []
---

# 示例项目

> **TL;DR:** 示例。
""",
            encoding="utf-8",
        )
        git(self.kb, "init")
        git(self.kb, "config", "user.email", "test@example.test")
        git(self.kb, "config", "user.name", "Tests")
        git(self.kb, "config", "gc.auto", "0")
        git(self.kb, "config", "maintenance.auto", "false")
        git(self.kb, "add", ".")
        git(self.kb, "commit", "-m", "init")

    def tearDown(self):
        self.temp.cleanup()

    def test_audit_and_plan_are_read_only_and_default_to_not_apply(self):
        before = git(self.kb, "status", "--short")
        audit = audit_kb(self.kb)
        plan = build_plan(self.kb)
        self.assertEqual("read-only", audit["mode"])
        self.assertEqual(1, audit["nodes"]["unambiguous_primary_candidate"])
        self.assertTrue(plan["raws"])
        self.assertTrue(all(not item["apply"] for item in plan["raws"]))
        self.assertTrue(all(not item["apply"] for item in plan["nodes"]))
        self.assertEqual(before, git(self.kb, "status", "--short"))

    def test_apply_adds_sidecar_and_primary_source_without_changing_raw(self):
        raw_before = self.raw.read_bytes()
        plan = build_plan(self.kb)
        plan["raws"][0]["apply"] = True
        node_config = next(item for item in plan["nodes"] if item["id"] == "project-example")
        node_config["apply"] = True
        plan_path = self.root / "plan.json"
        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

        receipt = apply_backfill(self.kb, plan_path)
        self.assertEqual("committed", receipt["status"])
        self.assertEqual(raw_before, self.raw.read_bytes())
        self.assertTrue(
            (self.kb / "provenance/raw-2026-07-20-project.json").is_file()
        )
        fm, body = parse_file(str(self.node))
        self.assertEqual("raw-2026-07-20-project", fm["primary_source"])
        self.assertEqual(
            "https://example.test/docx/project",
            fm["primary_source_url"],
        )
        self.assertNotIn("## 证据", body)
        self.assertEqual("", git(self.kb, "status", "--short"))


if __name__ == "__main__":
    unittest.main()
