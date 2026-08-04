import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from dreaming_evaluation import evaluate_shadow  # noqa: E402
from dreaming_state import DreamingError  # noqa: E402


class DreamingEvaluationTests(unittest.TestCase):
    def write_evaluation(self, root: Path):
        samples = [
            {
                "sample_id": "S-1",
                "priority": "P0",
                "slices": ["p2p"],
                "expected": True,
            },
            {
                "sample_id": "S-2",
                "priority": "P1",
                "slices": ["muted"],
                "expected": True,
            },
            {
                "sample_id": "S-3",
                "priority": "P2",
                "slices": ["group"],
                "expected": False,
            },
        ]
        (root / "golden.json").write_text(
            json.dumps({"samples": samples}),
            encoding="utf-8",
        )
        (root / "legacy.json").write_text(
            json.dumps(
                {
                    "predictions": [
                        {"sample_id": "S-1", "selected": True},
                        {"sample_id": "S-2", "selected": True},
                        {"sample_id": "S-3", "selected": False},
                    ]
                }
            ),
            encoding="utf-8",
        )
        (root / "dreaming.json").write_text(
            json.dumps(
                {
                    "predictions": [
                        {"sample_id": "S-1", "selected": True},
                        {"sample_id": "S-2", "selected": False},
                        {"sample_id": "S-3", "selected": True},
                    ]
                }
            ),
            encoding="utf-8",
        )

    def test_metrics_contain_ids_not_business_text(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            kb = root / "kb"
            skill = root / "skill"
            evaluation = root / "private-eval"
            kb.mkdir()
            skill.mkdir()
            evaluation.mkdir()
            self.write_evaluation(evaluation)
            result = evaluate_shadow(
                kb=kb,
                skill_root=skill,
                evaluation_dir=evaluation,
            )
            self.assertEqual(0.5, result["metrics"]["recall"])
            self.assertEqual(["S-2"], result["legacy_p0_p1_regression_ids"])
            self.assertFalse(result["gate_passed"])
            self.assertFalse(result["dataset_ready"])
            metrics = json.loads(
                (evaluation / "metrics.json").read_text(encoding="utf-8")
            )
            self.assertEqual(result, metrics)

    def test_rejects_business_text_and_repo_paths(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            kb = root / "kb"
            skill = root / "skill"
            evaluation = root / "private-eval"
            kb.mkdir()
            skill.mkdir()
            evaluation.mkdir()
            self.write_evaluation(evaluation)
            golden = json.loads((evaluation / "golden.json").read_text())
            golden["samples"][0]["text"] = "secret"
            (evaluation / "golden.json").write_text(json.dumps(golden))
            with self.assertRaises(DreamingError):
                evaluate_shadow(
                    kb=kb,
                    skill_root=skill,
                    evaluation_dir=evaluation,
                )
            with self.assertRaises(DreamingError):
                evaluate_shadow(
                    kb=kb,
                    skill_root=skill,
                    evaluation_dir=skill,
                )

    def test_product_gate_requires_two_work_weeks(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            kb = root / "kb"
            skill = root / "skill"
            evaluation = root / "private-eval"
            kb.mkdir()
            skill.mkdir()
            evaluation.mkdir()
            self.write_evaluation(evaluation)
            slices = [
                "decision",
                "assignment",
                "risk",
                "short_reply",
                "p2p",
                "muted",
                "low_activity",
                "unreadable_attachment",
                "partial_coverage",
            ]
            samples = []
            for index in range(200):
                samples.append(
                    {
                        "sample_id": f"S-{index}",
                        "priority": "P0" if index < 20 else "P1",
                        "slices": [slices[index % len(slices)]],
                        "expected": True,
                    }
                )
            predictions = [
                {"sample_id": sample["sample_id"], "selected": True}
                for sample in samples
            ]
            (evaluation / "golden.json").write_text(
                json.dumps({"samples": samples}),
                encoding="utf-8",
            )
            for name in ("legacy", "dreaming"):
                (evaluation / f"{name}.json").write_text(
                    json.dumps({"predictions": predictions}),
                    encoding="utf-8",
                )
            day = datetime(2026, 8, 3, 12, tzinfo=timezone.utc)
            result = None
            emitted = 0
            while emitted < 10:
                if day.weekday() < 5:
                    result = evaluate_shadow(
                        kb=kb,
                        skill_root=skill,
                        evaluation_dir=evaluation,
                        now=day,
                    )
                    emitted += 1
                day += timedelta(days=1)
            self.assertIsNotNone(result)
            self.assertTrue(
                result["product_gate"]["eligible_for_inbox_removal"]
            )


if __name__ == "__main__":
    unittest.main()
