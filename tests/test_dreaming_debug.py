import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from dreaming_debug import inspect_run  # noqa: E402
from dreaming_run_log import append_run_event  # noqa: E402
from dreaming_run_result import save_run_result  # noqa: E402


class DreamingDebugTests(unittest.TestCase):
    def make_kb(self, root: Path) -> Path:
        kb = root / "kb"
        (kb / ".git" / "info").mkdir(parents=True)
        return kb

    def write_json(self, path: Path, value) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def test_process_run_projects_findings_and_source_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            batch_id = "EB-" + "a" * 32
            run_id = "DR-audit"
            started = datetime(2026, 8, 6, 2, 0, tzinfo=timezone.utc)
            item_id = "message:om_test"
            content_name = "a" * 64 + ".json"
            batch = kb / "state" / "dreaming" / "batches" / batch_id
            self.write_json(
                batch / "manifest.json",
                {
                    "schema_version": "byteworker-evidence-batch/v1",
                    "batch_id": batch_id,
                    "created_at": (started + timedelta(minutes=1)).isoformat(),
                    "source": {"source_type": "feishu_chat", "lane": "discovery"},
                    "window": {
                        "observed_start": "2026-08-06 09:00",
                        "observed_end": "2026-08-06 10:00",
                    },
                    "coverage": {"status": "best_effort", "gaps": []},
                    "items": [
                        {
                            "item_id": item_id,
                            "occurred_at": "2026-08-06 09:30",
                            "content_ref": f"spool://{batch_id}/{content_name}",
                        }
                    ],
                },
            )
            self.write_json(
                kb / "state" / "dreaming" / "spool" / batch_id / content_name,
                {
                    "message_id": "om_test",
                    "chat_name": "测试群",
                    "chat_type": "group",
                    "sender": {"name": "测试用户"},
                    "content": "这是用于人工复核的原始消息。",
                },
            )
            finding = {
                "finding_id": "F-test",
                "kind": "risk",
                "summary": "发现一个待确认风险",
                "why_it_matters": "可能影响交付",
                "confidence": "medium",
                "uncertainties": ["尚未确认责任人"],
                "evidence_refs": [item_id],
            }
            self.write_json(
                batch / "finding-bundle.json",
                {
                    "schema_version": "byteworker-finding-bundle/v1",
                    "batch_id": batch_id,
                    "findings": [finding],
                },
            )
            self.write_json(
                batch / "analysis.receipt.json",
                {"batch_id": batch_id, "stage": "analyzed", "finding_count": 1},
            )
            self.write_json(
                batch / "consolidation.receipt.json",
                {"batch_id": batch_id, "stage": "consolidated", "finding_count": 1},
            )
            self.write_json(
                batch / "batch.commit.json",
                {"batch_id": batch_id, "stage": "committed"},
            )
            append_run_event(
                kb,
                run_id=run_id,
                event="leased",
                job="process",
                period="2026-08-06",
                owner="test-host",
                epoch=1,
                stage="scheduled",
                status="running",
                now=started,
            )
            append_run_event(
                kb,
                run_id=run_id,
                event="completed",
                job="process",
                period="2026-08-06",
                owner="test-host",
                epoch=1,
                stage="complete",
                status="success",
                batch_id=batch_id,
                metrics={"item_count": 1, "finding_count": 1},
                now=started + timedelta(minutes=2),
            )

            result = inspect_run(kb, run_id=run_id)["result"]

            self.assertEqual("explicit", result["linkage"])
            projected = result["batches"][0]
            self.assertEqual(1, projected["item_count"])
            self.assertEqual(1, projected["finding_count"])
            self.assertTrue(all(check["status"] == "pass" for check in projected["checks"]))
            self.assertEqual(
                "这是用于人工复核的原始消息。",
                projected["findings"][0]["evidence"][0]["content"],
            )

    def test_diagnostic_run_projects_structured_checks(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            run_id = "DR-maintenance-audit"
            started = datetime(2026, 8, 6, 3, 30, tzinfo=timezone.utc)
            append_run_event(
                kb,
                run_id=run_id,
                event="leased",
                job="maintenance",
                period="2026-08-06",
                owner="test-host",
                epoch=1,
                stage="scheduled",
                status="running",
                now=started,
            )
            save_run_result(
                kb,
                document={
                    "schema_version": "byteworker-dreaming-run-result/v1",
                    "job": "maintenance",
                    "period": "2026-08-06",
                    "summary": "完成 doctor 扫描，并执行 1 项自动修复。",
                    "checks": [
                        {
                            "name": "doctor_errors",
                            "status": "pass",
                            "detail": "0 个 error",
                        }
                    ],
                    "repairs": [
                        {
                            "path": "INDEX.md",
                            "code": "INDEX_STALE",
                            "action": "rebuild_index",
                            "detail": "补回 1 个缺失节点。",
                        }
                    ],
                },
                job="maintenance",
                period="2026-08-06",
                run_id=run_id,
                now=started + timedelta(minutes=1),
            )
            append_run_event(
                kb,
                run_id=run_id,
                event="completed",
                job="maintenance",
                period="2026-08-06",
                owner="test-host",
                epoch=1,
                stage="complete",
                status="success",
                now=started + timedelta(minutes=2),
            )

            result = inspect_run(kb, run_id=run_id)["result"]

            self.assertEqual("diagnostic", result["kind"])
            self.assertEqual("完成 doctor 扫描，并执行 1 项自动修复。", result["summary"])
            self.assertEqual("doctor_errors", result["checks"][0]["name"])
            self.assertEqual("INDEX.md", result["repairs"][0]["path"])
            self.assertEqual("rebuild_index", result["repairs"][0]["action"])


if __name__ == "__main__":
    unittest.main()
