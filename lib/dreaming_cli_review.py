"""Dreaming finding review, feedback, explanation, and shadow evaluation CLI."""

from __future__ import annotations

import argparse
from pathlib import Path

from dreaming_cli_common import positive
from dreaming_consolidation import explain_finding, record_finding_feedback, review_findings
from dreaming_evaluation import evaluate_shadow


def register_commands(sub: argparse._SubParsersAction) -> None:
    review = sub.add_parser("review", help="List findings for human review")
    review.add_argument("--kb", required=True, type=Path)
    review.add_argument("--status", default="open", choices=("open", "snoozed", "resolved", "dismissed", "promoted", "all"))
    review.add_argument("--limit", type=positive, default=50)
    review.set_defaults(_handler=lambda args, kb, root: review_findings(kb, status=args.status, limit=args.limit))

    explain = sub.add_parser("explain", help="Explain one finding and its evidence")
    explain.add_argument("--kb", required=True, type=Path)
    explain.add_argument("finding_id")
    explain.set_defaults(_handler=lambda args, kb, root: explain_finding(kb, finding_id=args.finding_id))

    feedback = sub.add_parser("feedback", help="Record human feedback on a finding")
    feedback.add_argument("--kb", required=True, type=Path)
    feedback.add_argument("finding_id")
    feedback.add_argument("--status", required=True, choices=("open", "snoozed", "resolved", "dismissed", "promoted"))
    feedback.add_argument("--value", default="none", choices=("helpful", "unimportant", "already_known", "handled", "wrong_link", "none"))
    feedback.add_argument("--request-id", required=True)
    feedback.add_argument("--snooze-until", default="")
    feedback.set_defaults(_handler=_feedback)

    shadow = sub.add_parser("shadow", help="Run private shadow evaluation")
    shadow_sub = shadow.add_subparsers(dest="shadow_operation", required=True)
    evaluate = shadow_sub.add_parser("evaluate", help="Evaluate against a private fixture set")
    evaluate.add_argument("--kb", required=True, type=Path)
    evaluate.add_argument("--evaluation-dir", required=True, type=Path)
    evaluate.set_defaults(_handler=lambda args, kb, root: evaluate_shadow(kb=kb, skill_root=root, evaluation_dir=args.evaluation_dir))


def _feedback(args, kb: Path, root: Path):
    return record_finding_feedback(
        kb,
        finding_id=args.finding_id,
        status=args.status,
        value=args.value,
        request_id=args.request_id,
        snooze_until=args.snooze_until,
    )
