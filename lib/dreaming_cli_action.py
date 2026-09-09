"""Dreaming action planning, claim fencing, and reconciliation CLI."""

from __future__ import annotations

import argparse
from pathlib import Path

from dreaming_action_ledger import (
    cancel_action,
    claim_action,
    complete_action,
    load_downstream_receipt,
    plan_actions,
    reconcile_action,
    validate_claim,
)
from dreaming_action_policy import evaluate_action_plan, load_action_plan


def register_commands(sub: argparse._SubParsersAction) -> None:
    action = sub.add_parser("action", help="Plan and execute fenced Dreaming actions")
    action_sub = action.add_subparsers(dest="action_operation", required=True)
    plan = action_sub.add_parser("plan", help="Validate and persist an ActionPlan")
    plan.add_argument("--kb", required=True, type=Path)
    plan.add_argument("--input", required=True, type=Path)
    plan.add_argument("--lease-token", required=True)
    plan.set_defaults(_handler=_plan)
    claim = action_sub.add_parser("claim", help="Claim one planned action")
    claim.add_argument("--kb", required=True, type=Path)
    claim.add_argument("--action-id", required=True)
    claim.add_argument("--lease-token", required=True)
    claim.add_argument("--confirmed", action="store_true")
    claim.set_defaults(_handler=lambda args, kb, root: claim_action(kb, action_id=args.action_id, lease_token=args.lease_token, confirmed=args.confirmed))
    validate = action_sub.add_parser("validate-claim", help="Validate an action claim token")
    validate.add_argument("--kb", required=True, type=Path)
    validate.add_argument("--action-id", required=True)
    validate.add_argument("--claim-token", required=True)
    validate.set_defaults(_handler=lambda args, kb, root: validate_claim(kb, action_id=args.action_id, claim_token=args.claim_token))
    complete = action_sub.add_parser("complete", help="Complete an action from a downstream receipt")
    complete.add_argument("--kb", required=True, type=Path)
    complete.add_argument("--action-id", required=True)
    complete.add_argument("--claim-token", required=True)
    complete.add_argument("--receipt", required=True, type=Path)
    complete.set_defaults(_handler=_complete)
    cancel = action_sub.add_parser("cancel", help="Cancel a planned action")
    cancel.add_argument("--kb", required=True, type=Path)
    cancel.add_argument("--action-id", required=True)
    cancel.add_argument("--reason", required=True)
    cancel.set_defaults(_handler=lambda args, kb, root: cancel_action(kb, action_id=args.action_id, reason=args.reason))
    reconcile = action_sub.add_parser("reconcile", help="Reconcile action state with an optional receipt")
    reconcile.add_argument("--kb", required=True, type=Path)
    reconcile.add_argument("--action-id", required=True)
    reconcile.add_argument("--receipt", type=Path)
    reconcile.set_defaults(_handler=_reconcile)


def _plan(args, kb: Path, root: Path):
    proposed = load_action_plan(args.input, skill_root=root)
    return plan_actions(kb, plan=evaluate_action_plan(kb, plan=proposed), lease_token=args.lease_token)


def _complete(args, kb: Path, root: Path):
    return complete_action(
        kb,
        action_id=args.action_id,
        claim_token=args.claim_token,
        receipt=load_downstream_receipt(args.receipt, skill_root=root),
    )


def _reconcile(args, kb: Path, root: Path):
    receipt = load_downstream_receipt(args.receipt, skill_root=root) if args.receipt else None
    return reconcile_action(kb, action_id=args.action_id, receipt=receipt)
