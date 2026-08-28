import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from digest_flow import (  # noqa: E402
    capture_flow,
    commit_flow,
    flow_status,
    prepare_flow,
    start_flow,
)


class Result:
    def __init__(self, returncode=0):
        self.returncode = returncode
        self.stderr = b""


class DigestFlowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.kb = self.root / "kb"
        self.kb.mkdir()
        (self.kb / "knowledge").mkdir()
        (self.kb / "raw_data").mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.kb, check=True)
        self.source = self.root / "source.md"
        self.source.write_text("# Local source\n\nNo dependencies.\n", encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def start(self):
        return start_flow(
            self.kb,
            source_type="local_md",
            source_ref=str(self.source),
        )

    def bundle_request(self, work_dir: Path) -> Path:
        request = work_dir / "bundle-request.json"
        request.write_text(
            json.dumps(
                {
                    "source_uid": "local-flow-test",
                    "title": "Local flow test",
                    "local_file": {"path": str(self.source)},
                }
            ),
            encoding="utf-8",
        )
        return request

    def test_start_prepare_status_and_commit_close_deterministic_stages(self):
        started = self.start()
        run_id = started["run_id"]
        work_dir = Path(started["work_dir"])
        prepared = prepare_flow(
            self.kb,
            run_id=run_id,
            bundle_request=self.bundle_request(work_dir),
        )
        self.assertEqual("prepared", prepared["phase"])
        self.assertEqual("new_source", prepared["preflight_state"])
        self.assertEqual("semantic_analysis", prepared["next_action"])
        self.assertTrue(Path(prepared["analysis"]["packet_path"]).is_file())
        self.assertEqual(1, prepared["semantic_plan"]["shard_count"])

        status = flow_status(self.kb, run_id=run_id)
        self.assertEqual("prepared", status["phase"])
        self.assertEqual(
            ["classify", "bundle", "preflight", "analysis_prepare"],
            [item["stage"] for item in status["run"]["stages"]],
        )
        state_path = work_dir / "state.json"
        self.assertEqual(0o600, os.stat(state_path).st_mode & 0o777)
        self.assertEqual(0o700, os.stat(work_dir).st_mode & 0o777)

        plan = work_dir / "digest-plan.json"
        plan.write_text("{}\n", encoding="utf-8")

        def execute(kb, plan_path, skill_root):
            self.assertEqual(self.kb, kb)
            self.assertEqual(plan, plan_path)
            self.assertEqual(ROOT, skill_root)
            return {"status": "committed", "nodes": [{"id": "reading-1"}], "warnings": []}

        committed = commit_flow(
            self.kb,
            run_id=run_id,
            plan_path=plan,
            executor=execute,
        )
        self.assertEqual("committed", committed["phase"])
        final = flow_status(self.kb, run_id=run_id)
        self.assertEqual("committed", final["run"]["status"])
        self.assertEqual("transaction", final["run"]["stages"][-1]["stage"])

    def test_capture_wraps_stage_and_can_run_multiple_waves(self):
        started = self.start()
        run_id = started["run_id"]
        work_dir = Path(started["work_dir"])
        skill = self.root / "skill"
        (skill / "bin").mkdir(parents=True)
        (skill / "bin" / "byteworker").write_text("#!/bin/sh\n", encoding="utf-8")
        (skill / "bin" / "pull_doc_comments.py").write_text("", encoding="utf-8")

        def runner(command, *, stdout, **kwargs):
            stdout.write(b"captured")
            return Result()

        for index in range(2):
            request = work_dir / f"capture-{index}.json"
            output = work_dir / f"capture-{index}-output.json"
            request.write_text(
                json.dumps(
                    {
                        "schema_version": "byteworker-digest-capture-plan/v1",
                        "max_workers": 1,
                        "jobs": [
                            {
                                "id": f"body-{index}",
                                "runner": "lark",
                                "args": ["docs", "+fetch", "--doc", f"doc-{index}"],
                                "output": str(output),
                                "max_attempts": 1,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            receipt = capture_flow(
                self.kb,
                run_id=run_id,
                request_path=request,
                skill_root=skill,
                runner=runner,
                sleeper=lambda _: None,
            )
            self.assertEqual(index + 1, receipt["capture_count"])
            self.assertEqual("captured", output.read_text())

        stages = flow_status(self.kb, run_id=run_id)["run"]["stages"]
        self.assertEqual(2, sum(item["stage"] == "capture" for item in stages))

    def test_cli_start_and_status_use_one_public_facade(self):
        cli = ROOT / "bin" / "digest-flow.py"
        started = subprocess.run(
            [
                sys.executable,
                str(cli),
                "start",
                "--kb",
                str(self.kb),
                "--source-type",
                "local_md",
                "--source-ref",
                "opaque-source",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(0, started.returncode, msg=started.stderr)
        payload = json.loads(started.stdout)
        shown = subprocess.run(
            [
                sys.executable,
                str(cli),
                "status",
                "--kb",
                str(self.kb),
                "--run-id",
                payload["run_id"],
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(0, shown.returncode, msg=shown.stderr)
        self.assertEqual("classified", json.loads(shown.stdout)["phase"])


if __name__ == "__main__":
    unittest.main()
