#!/usr/bin/env python3
"""Manage report-scheduler onboarding state and cross-run execution leases."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from report_automation import (  # noqa: E402
    ReportAutomationError,
    acquire_lease,
    check_period,
    complete_run,
    configure,
    record_decision,
    status,
)


def _positive(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--pretty", action="store_true")
    sub = result.add_subparsers(dest="operation", required=True)

    status_parser = sub.add_parser("status")
    status_parser.add_argument("--kb", required=True, type=Path)

    decision = sub.add_parser("decision")
    decision.add_argument("--kb", required=True, type=Path)
    decision.add_argument(
        "--value",
        required=True,
        choices=("prompted", "declined", "deferred"),
    )

    setup = sub.add_parser("configure")
    setup.add_argument("--kb", required=True, type=Path)
    setup.add_argument("--harness", required=True)
    setup.add_argument("--timezone", required=True)
    setup.add_argument("--environment", choices=("local",), default="local")
    setup.add_argument("--daily-schedule", required=True)
    setup.add_argument("--weekly-schedule", required=True)
    setup.add_argument("--daily-task-id", default="")
    setup.add_argument("--weekly-task-id", default="")
    setup.add_argument("--recovery-schedule", default="")
    setup.add_argument("--recovery-task-id", default="")

    check = sub.add_parser("check")
    check.add_argument("--kb", required=True, type=Path)
    check.add_argument("--kind", required=True, choices=("daily", "weekly"))
    check.add_argument("--period", required=True)

    lease = sub.add_parser("lease")
    lease.add_argument("--kb", required=True, type=Path)
    lease.add_argument("--kind", required=True, choices=("daily", "weekly"))
    lease.add_argument("--period", required=True)
    lease.add_argument("--owner", required=True)
    lease.add_argument("--lease-seconds", type=_positive, default=7200)

    complete = sub.add_parser("complete")
    complete.add_argument("--kb", required=True, type=Path)
    complete.add_argument("--token", required=True)
    complete.add_argument("--run-status", required=True, choices=("success", "failed"))
    complete.add_argument("--report-path", default="")
    complete.add_argument("--error-code", default="")
    return result


def _validate_kb(value: Path) -> Path:
    kb = value.expanduser().resolve()
    if not kb.is_dir():
        raise ReportAutomationError(
            "REPORT_AUTOMATION_KB_INVALID",
            f"知识库目录不存在: {kb}",
        )
    if kb == ROOT or ROOT in kb.parents:
        raise ReportAutomationError(
            "REPORT_AUTOMATION_KB_INVALID",
            "自动报告状态不得写入 byteworker skill 仓库。",
        )
    return kb


def _run(args: argparse.Namespace) -> object:
    kb = _validate_kb(args.kb)
    if args.operation == "status":
        return status(kb)
    if args.operation == "decision":
        return record_decision(kb, decision=args.value)
    if args.operation == "configure":
        return configure(
            kb,
            harness=args.harness,
            timezone_name=args.timezone,
            environment=args.environment,
            daily_schedule=args.daily_schedule,
            weekly_schedule=args.weekly_schedule,
            daily_task_id=args.daily_task_id,
            weekly_task_id=args.weekly_task_id,
            recovery_schedule=args.recovery_schedule,
            recovery_task_id=args.recovery_task_id,
        )
    if args.operation == "check":
        return check_period(
            kb,
            kind=args.kind,
            period=args.period,
        )
    if args.operation == "lease":
        return acquire_lease(
            kb,
            kind=args.kind,
            period=args.period,
            owner=args.owner,
            lease_seconds=args.lease_seconds,
        )
    if args.operation == "complete":
        return complete_run(
            kb,
            token=args.token,
            run_status=args.run_status,
            report_path=args.report_path,
            error_code=args.error_code,
        )
    raise AssertionError(args.operation)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        value = _run(args)
        json.dump(
            value,
            sys.stdout,
            ensure_ascii=False,
            sort_keys=True,
            indent=2 if args.pretty else None,
        )
        sys.stdout.write("\n")
        return 0
    except ReportAutomationError as exc:
        json.dump(
            {"error": exc.as_dict()},
            sys.stdout,
            ensure_ascii=False,
            sort_keys=True,
            indent=2 if args.pretty else None,
        )
        sys.stdout.write("\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
