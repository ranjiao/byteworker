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

from digest_txn import (  # noqa: E402
    compute_payload,
    DigestTxnError,
    execute_batch_plan,
    execute_plan,
    preflight,
    sha256_file,
    validate_batch_plan,
    validate_plan,
)
from frontmatter import parse_file  # noqa: E402


NODE_DIRS = ("people", "projects", "areas", "orgs", "events", "decisions", "readings")


def run_git(kb: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(kb), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout.strip()


class DigestTxnTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name)
        self.kb = self.root / "kb"
        self.inputs = self.root / "inputs"
        self.kb.mkdir()
        self.inputs.mkdir()
        (self.kb / "raw_data").mkdir()
        (self.kb / "journal").mkdir()
        for directory in NODE_DIRS:
            (self.kb / "knowledge" / directory).mkdir(parents=True, exist_ok=True)
        (self.kb / "INDEX.md").write_text("# 知识库索引\n", encoding="utf-8")
        run_git(self.kb, "init")
        run_git(self.kb, "config", "user.email", "digest-tests@example.test")
        run_git(self.kb, "config", "user.name", "Digest Tests")
        run_git(self.kb, "config", "gc.auto", "0")
        run_git(self.kb, "config", "maintenance.auto", "false")
        run_git(self.kb, "add", "INDEX.md")
        run_git(self.kb, "commit", "-m", "init kb")

    def tearDown(self):
        self.temp.cleanup()

    def write_source(self, suffix="", comments=None):
        body = self.inputs / f"body{suffix}.xml"
        comments_path = self.inputs / f"comments{suffix}.json"
        body.write_text("<title>测试方案</title>\n<p>正文逐字内容</p>\n", encoding="utf-8")
        comments = [] if comments is None else comments
        comments_path.write_text(
            json.dumps(
                {
                    "coverage": {"status": "complete"},
                    "comments": comments,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return body, comments_path

    def source_manifest(self, body: Path, comments: Path, comment_count=0):
        return {
            "type": "feishu_doc",
            "uid": "doc-test-1",
            "revision": "1",
            "url": "https://example.test/docx/doc-test-1",
            "title": "测试方案",
            "comments_status": "complete",
            "comment_count": comment_count,
            "components": [
                {
                    "name": "body",
                    "kind": "body",
                    "path": str(body),
                    "mode": "verbatim",
                },
                {
                    "name": "comments",
                    "kind": "comments",
                    "path": str(comments),
                    "json_pointer": "/comments",
                    "mode": "canonical-json",
                    "coverage": "complete",
                },
            ],
        }

    def reading_text(self, raw_ids, title="测试方案", links=None):
        links = [] if links is None else links
        return """---
id: reading-test-plan
title: {title}
type: reading
tags: [test]
status: current
created: 2026-07-27
updated: 2026-07-27
last_verified: 2026-07-27
sources:
{sources}
links: [{links}]
---

# {title}

> **TL;DR:** 测试资料。

## 核心观点
- 测试。[E1]
""".format(
            title=title,
            sources="".join(f"  - {raw_id}\n" for raw_id in raw_ids).rstrip(),
            links=", ".join(links),
        )

    def write_plan(
        self,
        source,
        raw_id,
        raw_name,
        candidate_text,
        op="create",
        base_sha256="",
    ):
        candidate = self.inputs / f"{raw_name}-candidate.md"
        candidate.write_text(candidate_text, encoding="utf-8")
        node = {
            "op": op,
            "path": "knowledge/readings/reading-test-plan.md",
            "candidate": str(candidate),
            "primary_source": raw_id,
            "evidence": [
                {"id": "E1", "raw_id": raw_id, "anchor_id": "source"}
            ],
        }
        if base_sha256:
            node["base_sha256"] = base_sha256
        plan = {
            "schema_version": "digest-plan/v1",
            "source": source,
            "raw": {
                "raw_id": raw_id,
                "path": f"raw_data/{raw_name}.md",
            },
            "provenance": {"anchors": []},
            "nodes": [node],
            "journal": {"summary": f"《{source['title']}》"},
            "commit": {"message": f"digest {source['title']}"},
        }
        plan_path = self.inputs / f"{raw_name}-plan.json"
        plan_path.write_text(
            json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return plan_path

    def test_execute_then_preflight_is_noop_and_raw_preserves_body(self):
        body, comments = self.write_source()
        source = self.source_manifest(body, comments)
        raw_id = "raw-2026-07-27-test-plan"
        plan_path = self.write_plan(
            source,
            raw_id,
            "2026-07-27-test-plan",
            self.reading_text([raw_id]),
        )

        before = preflight(self.kb, source, plan_path)
        self.assertEqual("new_source", before["state"])
        receipt = execute_plan(self.kb, plan_path, ROOT)
        self.assertEqual("committed", receipt["status"])
        self.assertEqual(["reading-test-plan"], receipt["created"])

        raw_path = self.kb / "raw_data/2026-07-27-test-plan.md"
        raw_text = raw_path.read_text(encoding="utf-8")
        self.assertIn(body.read_text(encoding="utf-8"), raw_text)
        raw_fm, _ = parse_file(str(raw_path))
        self.assertEqual("digested", raw_fm["digest_status"])
        self.assertEqual(["reading-test-plan"], raw_fm["digest_targets"])

        after = preflight(self.kb, source, plan_path)
        self.assertEqual("noop", after["state"])
        self.assertEqual(raw_id, after["existing"]["raw_id"])
        self.assertEqual("", run_git(self.kb, "status", "--short"))

    def test_comment_only_change_updates_same_node(self):
        body, comments = self.write_source()
        source = self.source_manifest(body, comments)
        first_raw = "raw-2026-07-27-test-plan"
        first_plan = self.write_plan(
            source,
            first_raw,
            "2026-07-27-test-plan",
            self.reading_text([first_raw]),
        )
        execute_plan(self.kb, first_plan, ROOT)

        _, changed_comments = self.write_source(
            "-v2",
            comments=[{"comment_id": "c1", "text": "新增意见"}],
        )
        changed_source = self.source_manifest(body, changed_comments, comment_count=1)
        changed_source["revision"] = "1"
        second_raw = "raw-2026-07-27-test-plan-comments-v2"
        node_path = self.kb / "knowledge/readings/reading-test-plan.md"
        second_plan = self.write_plan(
            changed_source,
            second_raw,
            "2026-07-27-test-plan-comments-v2",
            self.reading_text([second_raw, first_raw], title="测试方案（评论更新）"),
            op="update",
            base_sha256=sha256_file(node_path),
        )

        self.assertEqual("new_version", preflight(self.kb, changed_source, second_plan)["state"])
        receipt = execute_plan(self.kb, second_plan, ROOT)
        self.assertEqual(["reading-test-plan"], receipt["updated"])
        self.assertEqual(2, len(list((self.kb / "raw_data").glob("*.md"))))
        self.assertEqual(
            1,
            len(list((self.kb / "knowledge/readings").glob("reading-test-plan.md"))),
        )

    def test_whiteboard_component_is_versioned_independently(self):
        body, comments = self.write_source()
        whiteboard = self.inputs / "whiteboard.json"
        whiteboard.write_text(
            json.dumps({"nodes": [{"id": "n1", "text": "A"}]}),
            encoding="utf-8",
        )
        source = self.source_manifest(body, comments)
        source["whiteboards_status"] = "complete"
        source["components"].append(
            {
                "name": "whiteboard:token-1",
                "kind": "whiteboard",
                "uid": "token-1",
                "path": str(whiteboard),
                "mode": "canonical-json",
                "coverage": "complete",
            }
        )
        raw_id = "raw-2026-07-27-test-plan"
        plan = self.write_plan(
            source,
            raw_id,
            "2026-07-27-test-plan",
            self.reading_text([raw_id]),
        )
        execute_plan(self.kb, plan, ROOT)
        raw_fm, _ = parse_file(str(self.kb / "raw_data/2026-07-27-test-plan.md"))
        self.assertEqual("1", raw_fm["embedded_whiteboards"])
        self.assertTrue(raw_fm["whiteboard_hash"].startswith("sha256:"))

        whiteboard.write_text(
            json.dumps({"nodes": [{"id": "n1", "text": "B"}]}),
            encoding="utf-8",
        )
        self.assertEqual("new_version", preflight(self.kb, source, plan)["state"])

    def test_chat_source_derives_progress_fields_for_index(self):
        transcript = self.inputs / "chat.txt"
        transcript.write_text("甲 [ou_x] · 2026-07-27 10:00\n决定采用方案 A。\n", encoding="utf-8")
        source = {
            "type": "feishu_chat",
            "uid": "oc_test",
            "title": "测试群",
            "source_window": (
                "2026-07-27T09:00:00+08:00 .. "
                "2026-07-27T11:00:00+08:00"
            ),
            "components": [
                {
                    "name": "body",
                    "kind": "body",
                    "path": str(transcript),
                    "mode": "verbatim",
                }
            ],
        }
        raw_id = "raw-2026-07-27-test-chat"
        candidate = self.inputs / "event-candidate.md"
        candidate.write_text(
            """---
id: event-2026-07-27-test-chat
title: 测试群讨论
type: event
tags: [test]
status: current
created: 2026-07-27
updated: 2026-07-27
last_verified: 2026-07-27
sources:
  - raw-2026-07-27-test-chat
links: []
---

# 测试群讨论

> **TL;DR:** 测试讨论。

## 结论
- 决定采用方案 A。[E1]
""",
            encoding="utf-8",
        )
        plan = {
            "schema_version": "digest-plan/v1",
            "source": source,
            "raw": {
                "raw_id": raw_id,
                "path": "raw_data/2026-07-27-test-chat.md",
            },
            "provenance": {"anchors": []},
            "nodes": [
                {
                    "op": "create",
                    "path": "knowledge/events/event-2026-07-27-test-chat.md",
                    "candidate": str(candidate),
                    "primary_source": raw_id,
                    "evidence": [
                        {"id": "E1", "raw_id": raw_id, "anchor_id": "source"}
                    ],
                }
            ],
            "journal": {"summary": "测试群讨论"},
            "commit": {"message": "digest test chat"},
        }
        plan_path = self.inputs / "chat-plan.json"
        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        execute_plan(self.kb, plan_path, ROOT)
        fm, _ = parse_file(str(self.kb / "raw_data/2026-07-27-test-chat.md"))
        self.assertEqual("oc_test", fm["source_chat_id"])
        self.assertEqual("测试群", fm["source_chat_name"])
        index = (self.kb / "INDEX.md").read_text(encoding="utf-8")
        self.assertIn("| 测试群 | oc_test | 2026-07-27T11:00:00+08:00 |", index)

    def test_update_rejects_changed_baseline(self):
        body, comments = self.write_source()
        source = self.source_manifest(body, comments)
        raw_id = "raw-2026-07-27-test-plan"
        first_plan = self.write_plan(
            source,
            raw_id,
            "2026-07-27-test-plan",
            self.reading_text([raw_id]),
        )
        execute_plan(self.kb, first_plan, ROOT)

        _, changed_comments = self.write_source(
            "-v2", comments=[{"comment_id": "c1"}]
        )
        changed_source = self.source_manifest(body, changed_comments, comment_count=1)
        second_raw = "raw-2026-07-27-test-plan-v2"
        second_plan = self.write_plan(
            changed_source,
            second_raw,
            "2026-07-27-test-plan-v2",
            self.reading_text([second_raw, raw_id]),
            op="update",
            base_sha256="sha256:" + "0" * 64,
        )
        with self.assertRaisesRegex(DigestTxnError, "节点基线已变化"):
            validate_plan(self.kb, second_plan)

    def test_standard_plan_requires_provenance(self):
        body, comments = self.write_source()
        source = self.source_manifest(body, comments)
        raw_id = "raw-2026-07-27-test-plan"
        plan_path = self.write_plan(
            source,
            raw_id,
            "2026-07-27-test-plan",
            self.reading_text([raw_id]),
        )
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        del plan["provenance"]
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        with self.assertRaisesRegex(DigestTxnError, "不允许省略来源链"):
            validate_plan(self.kb, plan_path)

    def test_update_rejects_implicit_source_removal(self):
        body, comments = self.write_source()
        source = self.source_manifest(body, comments)
        first_raw = "raw-2026-07-27-test-plan"
        first_plan = self.write_plan(
            source,
            first_raw,
            "2026-07-27-test-plan",
            self.reading_text([first_raw]),
        )
        execute_plan(self.kb, first_plan, ROOT)

        changed_body, changed_comments = self.write_source("-v2")
        changed_source = self.source_manifest(changed_body, changed_comments)
        changed_source["revision"] = "2"
        second_raw = "raw-2026-07-27-test-plan-v2"
        second_plan = self.write_plan(
            changed_source,
            second_raw,
            "2026-07-27-test-plan-v2",
            self.reading_text([second_raw]),
            op="update",
            base_sha256=sha256_file(
                self.kb / "knowledge/readings/reading-test-plan.md"
            ),
        )
        with self.assertRaisesRegex(DigestTxnError, "source_removal"):
            validate_plan(self.kb, second_plan)

    def test_batch_executes_two_sources_in_one_commit(self):
        body_a, comments_a = self.write_source("-a")
        body_b, comments_b = self.write_source("-b")
        source_a = self.source_manifest(body_a, comments_a)
        source_b = self.source_manifest(body_b, comments_b)
        source_b["uid"] = "doc-test-2"
        source_b["url"] = "https://example.test/docx/doc-test-2"
        source_b["title"] = "测试方案二"
        raw_a = "raw-2026-07-27-test-a"
        raw_b = "raw-2026-07-27-test-b"
        candidate = self.inputs / "batch-candidate.md"
        candidate.write_text(
            self.reading_text([raw_a, raw_b]).replace(
                "- 测试。[E1]", "- 来源一。[E1]\n- 来源二。[E2]"
            ),
            encoding="utf-8",
        )
        plan = {
            "schema_version": "digest-batch-plan/v1",
            "inputs": [
                {
                    "source": source_a,
                    "raw": {"raw_id": raw_a, "path": "raw_data/test-a.md"},
                    "provenance": {"anchors": []},
                },
                {
                    "source": source_b,
                    "raw": {"raw_id": raw_b, "path": "raw_data/test-b.md"},
                    "provenance": {"anchors": []},
                },
            ],
            "nodes": [
                {
                    "op": "create",
                    "path": "knowledge/readings/reading-test-plan.md",
                    "candidate": str(candidate),
                    "source_raw_ids": [raw_a, raw_b],
                    "primary_source": raw_a,
                    "evidence": [
                        {"id": "E1", "raw_id": raw_a, "anchor_id": "source"},
                        {"id": "E2", "raw_id": raw_b, "anchor_id": "source"},
                    ],
                }
            ],
            "journal": {"summary": "两份测试资料"},
            "commit": {"message": "digest test batch"},
        }
        plan_path = self.inputs / "batch-plan.json"
        plan_path.write_text(
            json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        validated = validate_batch_plan(self.kb, plan_path)
        self.assertEqual(2, len(validated.inputs))
        before = run_git(self.kb, "rev-list", "--count", "HEAD")
        receipt = execute_batch_plan(self.kb, plan_path, ROOT)
        after = run_git(self.kb, "rev-list", "--count", "HEAD")
        self.assertEqual("committed", receipt["status"])
        self.assertEqual(2, receipt["batch_size"])
        self.assertEqual(int(before) + 1, int(after))
        self.assertTrue((self.kb / "raw_data/test-a.md").exists())
        self.assertTrue((self.kb / "raw_data/test-b.md").exists())

    def test_new_link_requires_reverse_candidate(self):
        project = self.kb / "knowledge/projects/project-existing.md"
        project.write_text(
            """---
id: project-existing
title: Existing
type: project
status: current
created: 2026-07-27
updated: 2026-07-27
last_verified: 2026-07-27
sources: [https://example.test/source]
links: []
---

# Existing

> **TL;DR:** existing.
""",
            encoding="utf-8",
        )
        run_git(self.kb, "add", "knowledge/projects/project-existing.md")
        run_git(self.kb, "commit", "-m", "add project")

        body, comments = self.write_source()
        source = self.source_manifest(body, comments)
        raw_id = "raw-2026-07-27-test-plan"
        plan = self.write_plan(
            source,
            raw_id,
            "2026-07-27-test-plan",
            self.reading_text([raw_id], links=["project-existing"]),
        )
        with self.assertRaisesRegex(DigestTxnError, "缺少反向边"):
            validate_plan(self.kb, plan)

    def test_execute_preserves_unrelated_dirty_file(self):
        unrelated = self.kb / ".DS_Store"
        unrelated.write_text("user change", encoding="utf-8")
        body, comments = self.write_source()
        source = self.source_manifest(body, comments)
        raw_id = "raw-2026-07-27-test-plan"
        plan = self.write_plan(
            source,
            raw_id,
            "2026-07-27-test-plan",
            self.reading_text([raw_id]),
        )
        execute_plan(self.kb, plan, ROOT)
        status = run_git(self.kb, "status", "--short")
        self.assertIn("?? .DS_Store", status)
        self.assertEqual("user change", unrelated.read_text(encoding="utf-8"))

    def test_failure_after_writes_rolls_back_all_targets(self):
        body, comments = self.write_source()
        source = self.source_manifest(body, comments)
        raw_id = "raw-2026-07-27-test-plan"
        plan = self.write_plan(
            source,
            raw_id,
            "2026-07-27-test-plan",
            self.reading_text([raw_id]),
        )
        missing_skill_root = self.root / "missing-skill"
        with self.assertRaisesRegex(DigestTxnError, "INDEX 重建失败"):
            execute_plan(self.kb, plan, missing_skill_root)
        self.assertFalse((self.kb / "raw_data/2026-07-27-test-plan.md").exists())
        self.assertFalse(
            (self.kb / "knowledge/readings/reading-test-plan.md").exists()
        )
        self.assertEqual("# 知识库索引\n", (self.kb / "INDEX.md").read_text())
        self.assertEqual("", run_git(self.kb, "status", "--short"))

    def test_commit_failure_restores_files_and_git_index(self):
        hook = self.kb / ".git/hooks/pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)
        body, comments = self.write_source()
        source = self.source_manifest(body, comments)
        raw_id = "raw-2026-07-27-test-plan"
        plan = self.write_plan(
            source,
            raw_id,
            "2026-07-27-test-plan",
            self.reading_text([raw_id]),
        )
        with self.assertRaisesRegex(DigestTxnError, "git commit"):
            execute_plan(self.kb, plan, ROOT)
        self.assertFalse((self.kb / "raw_data/2026-07-27-test-plan.md").exists())
        self.assertFalse(
            (self.kb / "knowledge/readings/reading-test-plan.md").exists()
        )
        self.assertEqual("", run_git(self.kb, "diff", "--cached", "--name-only"))
        self.assertEqual("", run_git(self.kb, "status", "--short"))

    def test_rejects_target_path_escape(self):
        body, comments = self.write_source()
        source = self.source_manifest(body, comments)
        raw_id = "raw-2026-07-27-test-plan"
        plan_path = self.write_plan(
            source,
            raw_id,
            "2026-07-27-test-plan",
            self.reading_text([raw_id]),
        )
        plan = json.loads(plan_path.read_text())
        plan["raw"]["path"] = "../escaped.md"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        with self.assertRaisesRegex(DigestTxnError, "相对路径"):
            validate_plan(self.kb, plan_path)

    def test_complete_comments_requires_real_component_and_count(self):
        body, comments = self.write_source()
        source = self.source_manifest(body, comments, comment_count=1)
        raw_id = "raw-2026-07-27-test-plan"
        plan = self.write_plan(
            source,
            raw_id,
            "2026-07-27-test-plan",
            self.reading_text([raw_id]),
        )
        with self.assertRaisesRegex(DigestTxnError, "comment_count 不一致"):
            validate_plan(self.kb, plan)

    def test_canonical_json_hash_ignores_formatting_and_key_order(self):
        body, comments_a = self.write_source()
        comments_b = self.inputs / "comments-reformatted.json"
        comments_b.write_text(
            '{"comments":[],"coverage":{"status":"complete"}}\n',
            encoding="utf-8",
        )
        source_a = self.source_manifest(body, comments_a)
        source_b = self.source_manifest(body, comments_b)
        payload_a = compute_payload(source_a, self.inputs / "a.json")
        payload_b = compute_payload(source_b, self.inputs / "b.json")
        self.assertEqual(payload_a["comment_hash"], payload_b["comment_hash"])
        self.assertEqual(payload_a["content_hash"], payload_b["content_hash"])

    def test_preflight_accepts_legacy_component_hashes_without_migrating_raw(self):
        body, comments = self.write_source()
        source = self.source_manifest(body, comments)
        manifest_path = self.inputs / "source.json"
        manifest_path.write_text(json.dumps({"source": source}), encoding="utf-8")
        first = preflight(self.kb, source, manifest_path)
        payload = compute_payload(source, manifest_path)
        legacy_body_hash = next(
            value
            for value in payload["compatibility_hashes"]["body_hashes"]
            if value != first["body_hash"]
        )
        legacy_raw = self.kb / "raw_data/2026-07-01-legacy.md"
        legacy_raw.write_text(
            """---
raw_id: raw-2026-07-01-legacy
ingested: 2026-07-01T10:00:00+08:00
source_type: feishu_doc
source_uid: doc-test-1
source_revision: "1"
body_hash: {body_hash}
comment_hash: {comment_hash}
source_url: https://example.test/docx/doc-test-1
source_title: 测试方案
digest_status: digested
digest_targets:
  - reading-test-plan
---

legacy raw body
            """.format(
                body_hash=legacy_body_hash,
                comment_hash=first["comment_hash"],
            ),
            encoding="utf-8",
        )
        result = preflight(self.kb, source, manifest_path)
        self.assertEqual("noop", result["state"])
        self.assertEqual("raw-2026-07-01-legacy", result["existing"]["raw_id"])
        self.assertNotIn("payload_schema", parse_file(str(legacy_raw))[0])

    def test_execute_rejects_preexisting_staged_changes(self):
        staged = self.kb / "staged-note.md"
        staged.write_text("unrelated staged work\n", encoding="utf-8")
        run_git(self.kb, "add", "staged-note.md")
        body, comments = self.write_source()
        source = self.source_manifest(body, comments)
        raw_id = "raw-2026-07-27-test-plan"
        plan = self.write_plan(
            source,
            raw_id,
            "2026-07-27-test-plan",
            self.reading_text([raw_id]),
        )
        with self.assertRaisesRegex(DigestTxnError, "已有暂存变更"):
            execute_plan(self.kb, plan, ROOT)
        self.assertFalse((self.kb / "raw_data/2026-07-27-test-plan.md").exists())

    def test_execute_rejects_kb_with_remote(self):
        run_git(self.kb, "remote", "add", "origin", "https://example.test/private.git")
        body, comments = self.write_source()
        source = self.source_manifest(body, comments)
        raw_id = "raw-2026-07-27-test-plan"
        plan = self.write_plan(
            source,
            raw_id,
            "2026-07-27-test-plan",
            self.reading_text([raw_id]),
        )
        with self.assertRaisesRegex(DigestTxnError, "配置了 remote"):
            execute_plan(self.kb, plan, ROOT)
        self.assertFalse((self.kb / "raw_data/2026-07-27-test-plan.md").exists())


if __name__ == "__main__":
    unittest.main()
