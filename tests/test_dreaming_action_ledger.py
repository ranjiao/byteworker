from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from dreaming_action_ledger import (  # noqa: E402
    claim_action,
    complete_action,
    plan_actions,
    reconcile_action,
    validate_claim,
)
from dreaming_scheduler import enable, run_due  # noqa: E402
from dreaming_grants import set_action_grants  # noqa: E402
from dreaming_state import DreamingError  # noqa: E402


class DreamingActionLedgerTests(unittest.TestCase):
    def lease(self, root: Path, *, seconds=60):
        kb = root / "kb"
        (kb / ".git" / "info").mkdir(parents=True)
        enabled_at = datetime(2026, 8, 4, 0, tzinfo=timezone.utc)
        enable(
            kb,
            harness="trae",
            timezone_name="Asia/Shanghai",
            acknowledge_machine_runtime=True,
            acknowledge_capability_tour=True,
            now=enabled_at,
        )
        lease = run_due(
            kb,
            owner="host",
            lease_seconds=seconds,
            now=enabled_at + timedelta(hours=2),
        )["lease"]
        return kb, lease, enabled_at + timedelta(hours=2)

    def plan(self, lease, *, confirmation=False):
        return {
            "schema_version": "byteworker-action-plan/v1",
            "run_id": lease["token"],
            "grant_revision": 0,
            "plan_sha256": "plan-sha",
            "actions": [
                {
                    "action_id": "A-1",
                    "kind": "conflict_review" if confirmation else "suppress",
                    "dedupe_key": "action:F-1",
                    "finding_id": "F-1",
                    "evidence_refs": ["message:om_1"],
                    "policy_result": "confirm" if confirmation else "allowed",
                    "policy_reasons": [],
                    "requires_confirmation": confirmation,
                    "requires_recapture": False,
                    "coverage": {"batches": [], "missing": []},
                }
            ],
        }

    def test_confirmation_claim_and_validate(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb, lease, now = self.lease(Path(temporary))
            planned = plan_actions(
                kb,
                plan=self.plan(lease, confirmation=True),
                lease_token=lease["token"],
                now=now,
            )
            self.assertEqual("awaiting_confirmation", planned["actions"][0]["status"])
            with self.assertRaises(DreamingError) as caught:
                claim_action(
                    kb,
                    action_id="A-1",
                    lease_token=lease["token"],
                    confirmed=False,
                    now=now + timedelta(seconds=1),
                )
            self.assertEqual(
                "DREAMING_ACTION_CONFIRMATION_REQUIRED",
                caught.exception.code,
            )
            claim = claim_action(
                kb,
                action_id="A-1",
                lease_token=lease["token"],
                confirmed=True,
                now=now + timedelta(seconds=1),
            )
            validated = validate_claim(
                kb,
                action_id="A-1",
                claim_token=claim["token"],
                now=now + timedelta(seconds=2),
            )
            self.assertEqual(claim["token"], validated["token"])
            set_action_grants(
                kb,
                persist_report=False,
                archive=False,
                instant_alert=False,
                now=now + timedelta(seconds=3),
            )
            with self.assertRaises(DreamingError) as caught:
                validate_claim(
                    kb,
                    action_id="A-1",
                    claim_token=claim["token"],
                    now=now + timedelta(seconds=4),
                )
            self.assertEqual("DREAMING_GRANT_STALE", caught.exception.code)
            reconciled = reconcile_action(
                kb,
                action_id="A-1",
                receipt={
                    "status": "noop",
                    "idempotency_key": "action:F-1",
                },
                now=now + timedelta(seconds=5),
            )
            self.assertEqual("committed", reconciled["status"])

    def test_expired_claim_is_reconciled_not_reclaimed(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb, lease, now = self.lease(Path(temporary), seconds=10)
            plan_actions(
                kb,
                plan=self.plan(lease),
                lease_token=lease["token"],
                now=now,
            )
            claim = claim_action(
                kb,
                action_id="A-1",
                lease_token=lease["token"],
                confirmed=False,
                now=now + timedelta(seconds=1),
            )
            with self.assertRaises(DreamingError):
                validate_claim(
                    kb,
                    action_id="A-1",
                    claim_token=claim["token"],
                    now=now + timedelta(seconds=11),
                )
            result = reconcile_action(
                kb,
                action_id="A-1",
                now=now + timedelta(seconds=11),
            )
            self.assertEqual("reconcile", result["status"])
            committed = reconcile_action(
                kb,
                action_id="A-1",
                receipt={
                    "status": "noop",
                    "idempotency_key": "action:F-1",
                },
                now=now + timedelta(seconds=12),
            )
            self.assertEqual("committed", committed["status"])

    def test_wrong_downstream_idempotency_key_is_rejected(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb, lease, now = self.lease(Path(temporary))
            plan_actions(kb, plan=self.plan(lease), lease_token=lease["token"], now=now)
            claim = claim_action(
                kb,
                action_id="A-1",
                lease_token=lease["token"],
                confirmed=False,
                now=now + timedelta(seconds=1),
            )
            with self.assertRaises(DreamingError) as caught:
                complete_action(
                    kb,
                    action_id="A-1",
                    claim_token=claim["token"],
                    receipt={"status": "committed", "idempotency_key": "wrong"},
                    now=now + timedelta(seconds=2),
                )
            self.assertEqual(
                "DREAMING_ACTION_RECEIPT_INVALID",
                caught.exception.code,
            )

    def test_dedupe_key_cannot_bind_two_actions(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb, lease, now = self.lease(Path(temporary))
            first = self.plan(lease)
            plan_actions(kb, plan=first, lease_token=lease["token"], now=now)
            second = self.plan(lease)
            second["actions"][0]["action_id"] = "A-2"
            with self.assertRaises(DreamingError) as caught:
                plan_actions(
                    kb,
                    plan=second,
                    lease_token=lease["token"],
                    now=now,
                )
            self.assertEqual(
                "DREAMING_ACTION_DEDUPE_CONFLICT",
                caught.exception.code,
            )

    def test_durable_receipt_requires_matching_real_git_commit(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb, lease, now = self.lease(Path(temporary))
            plan = self.plan(lease)
            plan["actions"][0].update(
                {
                    "kind": "include_report",
                    "dedupe_key": "report:F-1",
                }
            )
            plan_actions(kb, plan=plan, lease_token=lease["token"], now=now)
            claim = claim_action(
                kb,
                action_id="A-1",
                lease_token=lease["token"],
                confirmed=False,
                now=now + timedelta(seconds=1),
            )
            subprocess.run(["git", "-C", str(kb), "init"], check=True, stdout=subprocess.DEVNULL)
            subprocess.run(["git", "-C", str(kb), "config", "user.name", "test"], check=True)
            subprocess.run(["git", "-C", str(kb), "config", "user.email", "test@example.com"], check=True)
            report = kb / "reports" / "daily" / "2026-08-04.md"
            report.parent.mkdir(parents=True)
            report.write_text("report\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(kb), "add", "reports"], check=True)
            subprocess.run(
                ["git", "-C", str(kb), "commit", "-m", "report"],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            commit = subprocess.run(
                ["git", "-C", str(kb), "rev-parse", "HEAD"],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip()
            result = complete_action(
                kb,
                action_id="A-1",
                claim_token=claim["token"],
                receipt={
                    "status": "committed",
                    "idempotency_key": "report:F-1",
                    "commit": commit,
                },
                now=now + timedelta(seconds=2),
            )
            self.assertEqual("committed", result["status"])


if __name__ == "__main__":
    unittest.main()
