import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from digest_flow import (  # noqa: E402
    DigestFlowError,
    analyze_flow,
    capture_flow,
    commit_flow,
    flow_status,
    prepare_flow,
    start_flow,
)
from digest_feedback import feedback_draft  # noqa: E402
from digest_txn import compute_payload  # noqa: E402
from dreaming_digest_scan import _invoke  # noqa: E402
from dreaming_state import DreamingError  # noqa: E402
from sources import canonical_sha256  # noqa: E402
from sources.adapters.meego import build_meego_bundle  # noqa: E402


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

    def test_meego_flow_projects_committed_baseline_delta_before_planning(self):
        coordinates = {"project_key": "project", "view_id": "view"}
        uid = "meego:project:view"
        old = [
            {"work_item_id": "1", "name": "Stable", "description": "https://example.test/docx/stable",
             "work_item_attribute": {"role_members": [{"key": "owner", "members": ["a", "b"]}]}},
            {"work_item_id": "2", "name": "Previous", "status": "doing"},
            {"work_item_id": "3", "name": "Left view"},
        ]
        unchanged = {**old[0], "work_item_attribute": {"role_members": [{"key": "owner", "members": ["b", "a"]}]}}

        def snapshot(records):
            return {
                "schema_version": "byteworker-source-snapshot/v1", "source_type": "meego",
                "source_uid": uid, "coordinates": coordinates, "fields": ["name"], "records": records,
            }

        def prepare(records):
            started = start_flow(self.kb, source_type="meego", source_ref=uid)
            work = Path(started["work_dir"])
            current = snapshot(records)
            capture = {
                "schema_version": "byteworker-source-capture/v1", "source_type": "meego",
                "source_uid": uid, "title": "View", "source_url": "https://example.test/storyView/view",
                "coordinates": coordinates, "pagination": {"complete": True},
                "requested_fields": ["name"],
                "snapshot": current, "content_hash": canonical_sha256(current),
                "anchors": [
                    {"anchor_id": f"workitem:{r['work_item_id']}", "kind": "meego_workitem",
                     "precision": "exact", "locator": {**coordinates, "work_item_id": r["work_item_id"]},
                     "label": r["name"], "open_url": "https://example.test/storyView/view"}
                    for r in records
                ],
            }
            capture_path = work / "capture.json"
            capture_path.write_text(json.dumps(capture))
            bundle = build_meego_bundle(capture, capture_path=capture_path)
            bundle_path = work / "capture-bundle.json"
            bundle_path.write_text(json.dumps(bundle.to_dict()))
            return prepare_flow(self.kb, run_id=started["run_id"], bundle_input=bundle_path), work

        baseline, _ = prepare(old)
        self.assertEqual(3, baseline["analysis"]["meego_changes"]["baseline"])
        raw = self.kb / "raw_data" / "previous.md"
        raw.write_text(
            f"---\nraw_id: previous\ningested: 2026-11-12T01:00:00Z\nsource_type: meego\n"
            f"source_uid: {uid}\ndigest_status: digested\n---\n\n"
            f"```json\n{json.dumps(snapshot(old))}\n```\n"
        )
        subprocess.run(["git", "add", "raw_data/previous.md"], cwd=self.kb, check=True)
        subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.test",
                        "commit", "-qm", "Baseline"], cwd=self.kb, check=True)

        delta, work = prepare([
            unchanged, {**old[1], "status": "done"}, {"work_item_id": "4", "name": "New"},
        ])
        self.assertEqual(
            {"baseline": 0, "added": 1, "changed": 1, "left_view": 1, "unchanged": 1, "current_total": 3},
            delta["analysis"]["meego_changes"],
        )
        packet = json.loads((work / "analysis-packet.json").read_text())
        self.assertEqual(["$/records/1", "$/records/2"], [i["path"] for i in packet["sections"][0]["text_items"]])
        self.assertEqual({"workitem:2", "workitem:4"}, {
            a["anchor_id"] for a in packet["anchors"] if a["kind"] == "meego_workitem"
        })
        self.assertEqual([], packet["dependency_candidates"])
        bundle = json.loads((work / "source-bundle.json").read_text())
        self.assertEqual({"1", "2", "4"}, {r["record_id"] for r in bundle["record_index"]})

        no_analysis, _ = prepare([unchanged, old[1]])
        self.assertEqual(1, no_analysis["analysis"]["meego_changes"]["left_view"])
        self.assertEqual("candidate_generation", no_analysis["next_action"])
        self.assertNotIn("semantic_plan", no_analysis)
        self.assertNotIn("dependency_plan", no_analysis)

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

    def test_check_only_defers_analysis_until_requested(self):
        flow = self.start()
        run_id = flow["run_id"]
        folder = Path(flow["work_dir"])
        checked = prepare_flow(
            self.kb, run_id=run_id, bundle_request=self.bundle_request(folder), check_only=True,
        )
        self.assertEqual("checked", checked["phase"])
        self.assertFalse((folder / "analysis-packet.json").exists())
        self.assertEqual("prepared", analyze_flow(self.kb, run_id=run_id)["phase"])
        self.assertEqual("prepared", analyze_flow(self.kb, run_id=run_id)["phase"])
        self.assertEqual(1, sum(
            event["stage"] == "analysis_prepare" for event in flow_status(self.kb, run_id=run_id)["run"]["stages"]
        ))

    def test_rolling_document_compares_the_selected_window_and_whiteboard_nodes(self):
        uid = "rolling-doc"
        window = '<h1 id="current">20261106</h1><p id="p">Current decision</p><whiteboard token="current-board"/>'
        older = '<h1 id="older">10-30</h1><p>Old decision</p><whiteboard token="older-board"/>'
        stored_nodes = [{"id": "b", "text": "Two", "z_index": 2}, {"id": "a", "text": "One", "visible": True}]
        old_body = self.root / "stored-body.xml"
        old_body.write_text(window)
        old_board = self.root / "stored-board.json"
        old_board.write_text(json.dumps(stored_nodes))
        old_payload = compute_payload({
            "type": "feishu_doc", "uid": uid, "url": f"https://example.test/docx/{uid}", "title": "Rolling doc",
            "revision": "1", "comments_status": "unavailable", "whiteboards_status": "complete",
            "components": [
                {"path": str(old_body), "name": "body", "kind": "body", "mode": "verbatim"},
                {"path": str(old_board), "name": "whiteboard:current-board", "kind": "whiteboard",
                 "mode": "canonical-json"},
            ],
        }, old_body)
        stored_key = f"feishu_doc:{uid}:2026-11-06:{old_payload['content_hash']}"
        raw_path = self.kb / "raw_data" / "stored.md"
        board_hash = old_payload["component_hashes"]["whiteboard:current-board"]
        raw_path.write_text(
            f"---\nraw_id: stored\nsource_type: feishu_doc\nsource_uid: {uid}\n"
            f"ingested: 2026-11-06T10:00:00+08:00\ndigest_period: 2026-11-06\n"
            f"digest_status: digested\ndigest_key: {stored_key}\n"
            f"body_hash: {old_payload['body_hash']}\nwhiteboard_hash: {old_payload['whiteboard_hash']}\n"
            f"payload_components:\n  - whiteboard:current-board|whiteboard|{board_hash}\n---\n"
            f"{window}\n\n## Archived whiteboard\n\n```json\n"
            f"{old_payload['components'][1].data.decode()}\n```\n"
        )

        def prepare(body, nodes):
            started = start_flow(self.kb, source_type="feishu_doc", source_ref=uid)
            work = Path(started["work_dir"])
            body_path = work / "capture.xml"
            body_path.write_text(body)
            outline_path = work / "outline.xml"
            outline_path.write_text(window.split("</h1>")[0] + "</h1><h1 id=\"older\">10-30</h1>")
            fetch_scope = _invoke(
                ROOT, "digest-flow", "document-scope", "--kb", str(self.kb), "--run-id", started["run_id"],
                "--source-uid", uid, "--body", str(outline_path), "--outline",
            )
            self.assertEqual(
                {"scope": "range", "digest_period": "2026-11-06", "start_block_id": "current", "end_block_id": "older"},
                fetch_scope,
            )
            board_path = work / "board.json"
            board_path.write_text(json.dumps({"ok": True, "data": {"nodes": nodes}}))
            scope = _invoke(ROOT, "digest-flow", "document-scope", "--kb", str(self.kb),
                            "--run-id", started["run_id"], "--source-uid", uid, "--body", str(body_path))
            self.assertEqual(["current-board"], scope["whiteboard_tokens"])
            request = work / "request.json"
            request.write_text(json.dumps({
                "source_uid": uid, "source_url": f"https://example.test/docx/{uid}", "title": "Rolling doc",
                "revision": "2", "body": {"path": str(body_path)},
                "whiteboards": [{"name": f"whiteboard:{token}", "uid": token, "path": str(board_path)}
                                for token in ("current-board", "older-board")],
            }))
            result = _invoke(ROOT, "digest-flow", "prepare", "--kb", str(self.kb), "--run-id", started["run_id"],
                             "--routine", "--check-only", "--bundle-request", str(request))
            bundle = json.loads((work / "source-bundle.json").read_text())
            self.assertEqual(["body", "whiteboard:current-board"], [c["name"] for c in bundle["components"]])
            self.assertEqual("/data/nodes", bundle["components"][1]["json_pointer"])
            return result, scope, work

        unchanged, scope, work = prepare(window + older, list(reversed(stored_nodes)))
        self.assertEqual("2026-11-06", scope["digest_period"])
        self.assertEqual("noop", unchanged["phase"])
        self.assertEqual(stored_key, unchanged["receipt"]["digest_key"])
        self.assertFalse((work / "analysis-packet.json").exists())
        ranged, _, _ = prepare(f'<fragment mode="range">{window}</fragment>', list(reversed(stored_nodes)))
        self.assertEqual("noop", ranged["phase"])
        with self.assertRaises(DreamingError) as caught:
            prepare(f'<fragment mode="range">{window}<excerpt>Partial</excerpt></fragment>', stored_nodes)
        self.assertEqual("SOURCE_DOCUMENT_WINDOW_INCOMPLETE", caught.exception.code)

        modified, _, _ = prepare(window + older, [{**stored_nodes[0], "text": "Changed"}, stored_nodes[1]])
        self.assertEqual("new_version", modified["preflight_state"])
        modified_type, _, _ = prepare(window + older, [stored_nodes[0], {**stored_nodes[1], "visible": 1}])
        self.assertEqual("new_version", modified_type["preflight_state"])
        new_window, scope, work = prepare(window.replace("20261106", "20261113") + window + older, stored_nodes)
        self.assertEqual("2026-11-13", scope["digest_period"])
        self.assertEqual("new_source", new_window["preflight_state"])
        analyzed = analyze_flow(self.kb, run_id=new_window["run_id"])
        packet = json.loads(Path(analyzed["analysis"]["packet_path"]).read_text())
        self.assertNotIn("Old decision", json.dumps(packet))
        self.assertNotIn("20261106", json.dumps(packet))

    def test_routine_document_with_many_links_keeps_semantic_plan(self):
        def prepare(source_uid, link_count, *, routine):
            run = start_flow(self.kb, source_type="feishu_doc", source_ref=source_uid)
            work_dir = Path(run["work_dir"])
            body = work_dir / "body.xml"
            links = "".join(
                f'<a href="https://example.test/docx/reference-{index}">Reference {index}</a>'
                for index in range(link_count)
            )
            body.write_text(f"<p>The current decision is recorded here.</p>{links}", encoding="utf-8")
            request = work_dir / "bundle-request.json"
            request.write_text(json.dumps({
                "source_uid": source_uid, "source_url": f"https://example.test/docx/{source_uid}",
                "title": "Document", "revision": "1", "body": {"path": str(body)},
            }))
            prepare_flow(self.kb, run_id=run["run_id"], bundle_request=request, check_only=True)
            result = _invoke(
                ROOT, "digest-flow", "analyze", "--kb", str(self.kb), "--run-id", run["run_id"],
                *(("--routine",) if routine else ()),
            )
            return result, work_dir

        result, work_dir = prepare("routine", 33, routine=True)
        scope = {"mode": "source_only", "reason_code": "DIGEST_DEPENDENCY_SCOPE_LIMITED", "skipped_count": 33}
        self.assertEqual("semantic_analysis", result["next_action"])
        self.assertNotIn("dependency_plan", result)
        self.assertFalse((work_dir / "dependency").exists())
        self.assertEqual(scope, result["analysis"]["dependency_scope"])
        status = flow_status(self.kb, run_id=result["run_id"])
        self.assertEqual(scope, status["dependency_scope"])
        event = status["run"]["stages"][-1]
        self.assertEqual("completed", event["status"])
        self.assertEqual(33, event["metrics"]["skipped_dependency_count"])
        plan = json.loads(Path(result["semantic_plan"]["plan_path"]).read_text())
        shard = json.loads(Path(plan["shards"][0]["path"]).read_text())
        self.assertEqual(scope, shard["dependency_scope"])
        self.assertIn("The current decision is recorded here.", shard["sections"][0]["text_items"][0]["text"])
        self.assertIn("Reference 32", shard["sections"][0]["text_items"][0]["text"])

        for source_uid, count, routine in (("foreground", 33, False), ("small", 32, True)):
            with self.subTest(source_uid=source_uid):
                result, _ = prepare(source_uid, count, routine=routine)
                self.assertEqual("dependency_review", result["next_action"])
                self.assertIn("dependency_plan", result)
                self.assertEqual(count, result["analysis"]["dependency_candidate_count"])

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

    def test_committed_receipt_survives_failed_terminal_logging(self):
        started = self.start()
        run_id = started["run_id"]
        plan = Path(started["work_dir"]) / "digest-plan.json"
        plan.write_text("{}\n", encoding="utf-8")
        def executor(*_):
            return {"status": "committed"}

        with patch("digest_flow.finish_run", side_effect=OSError("log unavailable")):
            result = commit_flow(self.kb, run_id=run_id, plan_path=plan, executor=executor)
        self.assertEqual("committed", result["phase"])
        self.assertEqual("committed", result["receipt"]["status"])
        self.assertEqual("degraded", result["receipt"]["digest_run_logging"])
        with self.assertRaises(DigestFlowError) as caught:
            commit_flow(self.kb, run_id=run_id, plan_path=plan, executor=executor)
        self.assertEqual("DIGEST_FLOW_TERMINAL", caught.exception.code)
        draft = feedback_draft(self.kb, run_id=run_id, skill_root=ROOT)
        self.assertIn("事务结果：committed", draft["body"])
        self.assertIn("RUN_TERMINAL_NOT_RECORDED", draft["body"])

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
