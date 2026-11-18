from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from dreaming_run_brief import run_brief
from dreaming_run_result import read_run_result


class DreamingRunBriefTests(unittest.TestCase):
    def test_document_counts_exclude_pending_and_previous_checks_and_name_failures(self):
        doc = {
            "summary": "样例卡片增加了圆形。",
            "source_notes": {"ok": "示例记录标记了圆形。"},
            "accounting": {"routine_sources": [
                {"source_type": "feishu_doc", "label": "样例图册", "status": "committed", "source_key": "ok"},
                {"source_type": "local_md", "label": "样例手册", "status": "failed", "error_code": "SOURCE_PERMISSION_DENIED"},
                {"source_type": "feishu_doc", "label": "示例旧页", "status": "covered"},
                {"source_type": "feishu_doc", "label": "样例清单", "status": "pending"},
                {"source_type": "feishu_chat", "label": "示例群", "status": "empty_window"},
            ], "unaccounted_sources": 2},
        }
        brief = run_brief(Path("."), doc)
        for expected in ("样例卡片增加了圆形", "文档共 4 份，本次完成 1 份", "其他来源共 1 个", "样例手册　失败。没有读取权限",
                         "样例图册　已收录。示例记录", "示例旧页　此前已完成", "样例清单　尚未完成"):
            self.assertIn(expected, brief)
        self.assertNotIn("知识节点没有变化", brief)

    def test_legacy_recovery_reads_only_explicit_contemporaneous_persisted_finding(self):
        with tempfile.TemporaryDirectory() as temporary:
            kb = Path(temporary)
            root = kb / "state/dreaming"
            batch = "EB-" + "a" * 32
            directory = root / "batches" / batch
            directory.mkdir(parents=True)
            start = datetime(2026, 11, 9, tzinfo=timezone.utc)
            finish = start + timedelta(minutes=2)
            manifest = {"batch_id": batch, "source": {"lane": "discovery"}, "items": [{}, {}],
                        "created_at": start.isoformat(), "coverage": {"gaps": [{}, {}]}}
            (directory / "manifest.json").write_text(json.dumps(manifest))
            (directory / "batch.commit.json").write_text(json.dumps({
                "batch_id": batch, "stage": "committed", "committed_at": finish.isoformat(),
            }))
            (root / "finding-history.jsonl").write_text(json.dumps({
                "batch_id": batch, "operation": "upsert", "proposal": {"summary": "卡片新增了圆形。"},
            }))
            run = root / "run-results/DR-old.json"
            run.parent.mkdir()
            record = {"schema_version": "byteworker-dreaming-run-result/v1", "job": "recovery", "run_id": "DR-old",
                      "recorded_at": finish.isoformat(), "summary": "旧版内部步骤", "checks": [{"detail": batch}],
                      "accounting": {"metrics": {"duration_ms": 120000}}}
            run.write_text(json.dumps(record))
            before = run.read_bytes()
            brief = read_run_result(kb, "DR-old")["brief"]
            for expected in ("2 条历史消息", "卡片新增了圆形", "2 段消息未读全"):
                self.assertIn(expected, brief)
            self.assertNotIn("旧版内部步骤", brief)
            self.assertEqual(before, run.read_bytes())
            record["recorded_at"] = (finish + timedelta(days=1)).isoformat()
            self.assertEqual("旧版内部步骤", run_brief(kb, record))
