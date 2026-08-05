#!/usr/bin/env python3
"""Manage opt-in Dreaming scheduling without changing existing skill commands."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from dreaming_scheduler import (  # noqa: E402
    DreamingError,
    complete_run,
    configure,
    disable,
    enable,
    heartbeat_run,
    register_harness,
    renew_lease,
    retry_job,
    run_due,
    set_report_management,
    status,
    unregister_harness,
)
from dreaming_run_log import list_runs, show_run, tail_events  # noqa: E402
from dreaming_batch import abort_batch  # noqa: E402
from dreaming_collection import (  # noqa: E402
    prepare_foreground_im_batch,
    prepare_im_batch,
)
from dreaming_grants import set_im_grant  # noqa: E402
from dreaming_process import commit_finding_bundle  # noqa: E402
from dreaming_action_policy import evaluate_action_plan, load_action_plan  # noqa: E402
from dreaming_action_ledger import (  # noqa: E402
    cancel_action,
    claim_action,
    complete_action,
    load_downstream_receipt,
    plan_actions,
    reconcile_action,
    validate_claim,
)
from dreaming_reports import (  # noqa: E402
    complete_delivery,
    enqueue_delivery,
    prepare_report_packet,
    report_migration_readiness,
)
from dreaming_report_bundle import (  # noqa: E402
    load_report_document,
    render_report_bundle,
)
from dreaming_delivery_lark import deliver_lark_bot_summary  # noqa: E402
from dreaming_consolidation import (  # noqa: E402
    explain_finding,
    record_finding_feedback,
    review_findings,
)
from dreaming_evaluation import evaluate_shadow  # noqa: E402


def _positive(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _optional_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise argparse.ArgumentTypeError("must be true or false")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--pretty", action="store_true")
    sub = result.add_subparsers(dest="operation", required=True)

    status_parser = sub.add_parser("status")
    status_parser.add_argument("--kb", required=True, type=Path)

    configure_parser = sub.add_parser("configure")
    configure_parser.add_argument("--kb", required=True, type=Path)
    configure_parser.add_argument("--timezone")
    configure_parser.add_argument(
        "--process-kind",
        choices=("interval", "daily_time", "every_n_days"),
    )
    configure_parser.add_argument("--process-interval-minutes", type=_positive)
    configure_parser.add_argument("--process-time")
    configure_parser.add_argument("--process-every-days", type=_positive)
    configure_parser.add_argument("--process-enabled", type=_optional_bool)
    configure_parser.add_argument("--morning-time")
    configure_parser.add_argument("--morning-enabled", type=_optional_bool)
    configure_parser.add_argument("--maintenance-time")
    configure_parser.add_argument("--maintenance-enabled", type=_optional_bool)
    configure_parser.add_argument("--recovery-interval-minutes", type=_positive)
    configure_parser.add_argument("--recovery-enabled", type=_optional_bool)
    configure_parser.add_argument("--log-retention-days", type=_positive)
    configure_parser.add_argument("--lark-delivery-enabled", type=_optional_bool)
    configure_parser.add_argument("--lark-recipient-id")

    enable_parser = sub.add_parser("enable")
    enable_parser.add_argument("--kb", required=True, type=Path)
    enable_parser.add_argument("--harness", required=True)
    enable_parser.add_argument("--timezone", required=True)
    enable_parser.add_argument("--environment", choices=("local",), default="local")
    enable_parser.add_argument(
        "--acknowledge-capability-tour",
        action="store_true",
        help="确认已向用户完整介绍 Dreaming 能力、授权、成本和边界",
    )
    enable_parser.add_argument(
        "--acknowledge-schedule",
        action="store_true",
        help="确认已向用户展示并由用户确认完整运行计划",
    )
    enable_parser.add_argument(
        "--acknowledge-machine-runtime",
        action="store_true",
        help="确认机器需保持开机、唤醒、联网，且 Dreaming 会产生额外开销",
    )
    enable_parser.add_argument(
        "--process-kind",
        choices=("interval", "daily_time", "every_n_days"),
    )
    enable_parser.add_argument(
        "--process-interval-minutes",
        type=_positive,
    )
    enable_parser.add_argument("--process-time")
    enable_parser.add_argument("--process-every-days", type=_positive)
    enable_parser.add_argument("--morning-time")
    enable_parser.add_argument("--daily-time")
    enable_parser.add_argument(
        "--weekly-weekday",
        type=int,
        choices=range(7),
    )
    enable_parser.add_argument("--weekly-time")
    enable_parser.add_argument("--maintenance-time")
    enable_parser.add_argument(
        "--recovery-interval-minutes",
        type=_positive,
    )
    enable_parser.add_argument("--log-retention-days", type=_positive)

    disable_parser = sub.add_parser("disable")
    disable_parser.add_argument("--kb", required=True, type=Path)

    reports = sub.add_parser("manage-reports")
    reports.add_argument("--kb", required=True, type=Path)
    reports.add_argument("--enabled", choices=("true", "false"), required=True)
    reports.add_argument(
        "--acknowledge-owner-released",
        action="store_true",
        help="确认旧日报/周报 scheduler owner 已释放",
    )

    due = sub.add_parser("run-due")
    due.add_argument("--kb", required=True, type=Path)
    due.add_argument("--owner", required=True)
    due.add_argument("--lease-seconds", type=_positive, default=7200)

    renew = sub.add_parser("renew")
    renew.add_argument("--kb", required=True, type=Path)
    renew.add_argument("--token", required=True)
    renew.add_argument("--lease-seconds", type=_positive, default=7200)

    retry = sub.add_parser("retry-job")
    retry.add_argument("--kb", required=True, type=Path)
    retry.add_argument("--job", required=True)

    harness = sub.add_parser("harness")
    harness_sub = harness.add_subparsers(dest="harness_operation", required=True)
    harness_register = harness_sub.add_parser("register")
    harness_register.add_argument("--kb", required=True, type=Path)
    harness_register.add_argument("--task-id", required=True)
    harness_unregister = harness_sub.add_parser("unregister")
    harness_unregister.add_argument("--kb", required=True, type=Path)

    heartbeat = sub.add_parser("heartbeat")
    heartbeat.add_argument("--kb", required=True, type=Path)
    heartbeat.add_argument("--token", required=True)
    heartbeat.add_argument(
        "--stage",
        required=True,
        choices=(
            "scheduled",
            "collection",
            "analysis",
            "consolidation",
            "action",
            "report",
            "maintenance",
            "recovery",
            "complete",
        ),
    )
    heartbeat.add_argument("--detail-code", default="")
    heartbeat.add_argument("--progress-current", type=int)
    heartbeat.add_argument("--progress-total", type=int)

    runs = sub.add_parser("runs")
    runs_sub = runs.add_subparsers(dest="runs_operation", required=True)
    runs_list = runs_sub.add_parser("list")
    runs_list.add_argument("--kb", required=True, type=Path)
    runs_list.add_argument("--limit", type=_positive, default=50)
    runs_show = runs_sub.add_parser("show")
    runs_show.add_argument("--kb", required=True, type=Path)
    runs_show.add_argument("run_id")
    runs_tail = runs_sub.add_parser("tail")
    runs_tail.add_argument("--kb", required=True, type=Path)
    runs_tail.add_argument("--limit", type=_positive, default=50)
    runs_tail.add_argument("--run-id", default="")

    grant = sub.add_parser("grant")
    grant_sub = grant.add_subparsers(dest="grant_operation", required=True)
    set_im = grant_sub.add_parser("set-im")
    set_im.add_argument("--kb", required=True, type=Path)
    set_im.add_argument(
        "--mode",
        required=True,
        choices=("off", "monitored", "all_visible"),
    )
    set_im.add_argument("--persist-finding", action="store_true")
    set_im.add_argument("--acknowledge-all-visible", action="store_true")
    set_actions = grant_sub.add_parser("set-actions")
    set_actions.add_argument("--kb", required=True, type=Path)
    set_actions.add_argument("--persist-report", action="store_true")
    set_actions.add_argument("--archive", action="store_true")
    set_actions.add_argument("--instant-alert", action="store_true")

    process = sub.add_parser("process")
    process_sub = process.add_subparsers(dest="process_operation", required=True)
    prepare = process_sub.add_parser("prepare")
    prepare.add_argument("--kb", required=True, type=Path)
    prepare.add_argument("--source", choices=("im",), required=True)
    prepare.add_argument("--start", required=True)
    prepare.add_argument("--end", required=True)
    abort = process_sub.add_parser("abort")
    abort.add_argument("--kb", required=True, type=Path)
    abort.add_argument("--batch-id", required=True)
    abort.add_argument("--error-code", required=True)
    commit = process_sub.add_parser("commit")
    commit.add_argument("--kb", required=True, type=Path)
    commit.add_argument("--batch-id", required=True)
    commit.add_argument("--input", required=True, type=Path)
    commit.add_argument("--semantic-revision", default="finding-v1")
    once = process_sub.add_parser("once")
    once.add_argument("--kb", required=True, type=Path)
    once.add_argument("--source", choices=("im",), required=True)
    once.add_argument(
        "--mode",
        required=True,
        choices=("monitored", "all_visible"),
    )
    once.add_argument("--start", required=True)
    once.add_argument("--end", required=True)
    once.add_argument("--acknowledge-all-visible", action="store_true")

    review = sub.add_parser("review")
    review.add_argument("--kb", required=True, type=Path)
    review.add_argument(
        "--status",
        default="open",
        choices=("open", "snoozed", "resolved", "dismissed", "promoted", "all"),
    )
    review.add_argument("--limit", type=_positive, default=50)

    explain = sub.add_parser("explain")
    explain.add_argument("--kb", required=True, type=Path)
    explain.add_argument("finding_id")

    feedback = sub.add_parser("feedback")
    feedback.add_argument("--kb", required=True, type=Path)
    feedback.add_argument("finding_id")
    feedback.add_argument(
        "--status",
        required=True,
        choices=("open", "snoozed", "resolved", "dismissed", "promoted"),
    )
    feedback.add_argument(
        "--value",
        default="none",
        choices=(
            "helpful",
            "unimportant",
            "already_known",
            "handled",
            "wrong_link",
            "none",
        ),
    )
    feedback.add_argument("--request-id", required=True)
    feedback.add_argument("--snooze-until", default="")

    shadow = sub.add_parser("shadow")
    shadow_sub = shadow.add_subparsers(dest="shadow_operation", required=True)
    evaluate = shadow_sub.add_parser("evaluate")
    evaluate.add_argument("--kb", required=True, type=Path)
    evaluate.add_argument("--evaluation-dir", required=True, type=Path)

    report = sub.add_parser("report")
    report_sub = report.add_subparsers(dest="report_operation", required=True)
    report_prepare = report_sub.add_parser("prepare")
    report_prepare.add_argument("--kb", required=True, type=Path)
    report_prepare.add_argument(
        "--kind",
        required=True,
        choices=("morning", "daily", "weekly"),
    )
    report_prepare.add_argument("--period", required=True)
    render = report_sub.add_parser("render")
    render.add_argument("--kb", required=True, type=Path)
    render.add_argument("--input", required=True, type=Path)
    enqueue = report_sub.add_parser("enqueue-delivery")
    enqueue.add_argument("--kb", required=True, type=Path)
    enqueue.add_argument(
        "--kind",
        required=True,
        choices=("morning", "daily", "weekly"),
    )
    enqueue.add_argument("--period", required=True)
    enqueue.add_argument("--report-path", required=True)
    enqueue.add_argument("--commit", required=True)
    enqueue.add_argument(
        "--channel",
        choices=("host", "lark_bot"),
        default="host",
    )
    enqueue.add_argument(
        "--artifact",
        choices=("summary", "html", "markdown"),
        default="markdown",
    )
    enqueue.add_argument("--recipient-id", default="")
    deliver = report_sub.add_parser("deliver")
    deliver.add_argument("--kb", required=True, type=Path)
    deliver.add_argument("--outbox-id", required=True)
    delivered = report_sub.add_parser("delivery-complete")
    delivered.add_argument("--kb", required=True, type=Path)
    delivered.add_argument("--outbox-id", required=True)
    delivered.add_argument("--delivery-id", required=True)

    action = sub.add_parser("action")
    action_sub = action.add_subparsers(dest="action_operation", required=True)
    action_plan = action_sub.add_parser("plan")
    action_plan.add_argument("--kb", required=True, type=Path)
    action_plan.add_argument("--input", required=True, type=Path)
    action_plan.add_argument("--lease-token", required=True)
    action_claim = action_sub.add_parser("claim")
    action_claim.add_argument("--kb", required=True, type=Path)
    action_claim.add_argument("--action-id", required=True)
    action_claim.add_argument("--lease-token", required=True)
    action_claim.add_argument("--confirmed", action="store_true")
    action_validate = action_sub.add_parser("validate-claim")
    action_validate.add_argument("--kb", required=True, type=Path)
    action_validate.add_argument("--action-id", required=True)
    action_validate.add_argument("--claim-token", required=True)
    action_complete = action_sub.add_parser("complete")
    action_complete.add_argument("--kb", required=True, type=Path)
    action_complete.add_argument("--action-id", required=True)
    action_complete.add_argument("--claim-token", required=True)
    action_complete.add_argument("--receipt", required=True, type=Path)
    action_cancel = action_sub.add_parser("cancel")
    action_cancel.add_argument("--kb", required=True, type=Path)
    action_cancel.add_argument("--action-id", required=True)
    action_cancel.add_argument("--reason", required=True)
    action_reconcile = action_sub.add_parser("reconcile")
    action_reconcile.add_argument("--kb", required=True, type=Path)
    action_reconcile.add_argument("--action-id", required=True)
    action_reconcile.add_argument("--receipt", type=Path)

    complete = sub.add_parser("complete")
    complete.add_argument("--kb", required=True, type=Path)
    complete.add_argument("--token", required=True)
    complete.add_argument(
        "--run-status",
        required=True,
        choices=("success", "partial", "failed"),
    )
    complete.add_argument("--artifact-path", default="")
    complete.add_argument("--coverage-checkpoint", default="")
    complete.add_argument("--error-code", default="")
    complete.add_argument("--item-count", type=int)
    complete.add_argument("--finding-count", type=int)
    complete.add_argument("--gap-count", type=int)
    return result


def _validate_kb(value: Path) -> Path:
    kb = value.expanduser().resolve()
    if not kb.is_dir():
        raise DreamingError(
            "DREAMING_KB_INVALID",
            f"知识库目录不存在: {kb}",
        )
    if kb == ROOT or ROOT in kb.parents:
        raise DreamingError(
            "DREAMING_KB_INVALID",
            "Dreaming 状态不得写入 byteworker skill 仓库。",
        )
    return kb


def _run(args: argparse.Namespace) -> object:
    kb = _validate_kb(args.kb)
    if args.operation == "status":
        return status(kb)
    if args.operation == "configure":
        return configure(
            kb,
            timezone_name=args.timezone,
            process_kind=args.process_kind,
            process_interval_minutes=args.process_interval_minutes,
            process_time=args.process_time,
            process_every_days=args.process_every_days,
            process_enabled=args.process_enabled,
            morning_time=args.morning_time,
            morning_enabled=args.morning_enabled,
            maintenance_time=args.maintenance_time,
            maintenance_enabled=args.maintenance_enabled,
            recovery_interval_minutes=args.recovery_interval_minutes,
            recovery_enabled=args.recovery_enabled,
            log_retention_days=args.log_retention_days,
            lark_delivery_enabled=args.lark_delivery_enabled,
            lark_recipient_id=args.lark_recipient_id,
        )
    if args.operation == "enable":
        return enable(
            kb,
            harness=args.harness,
            timezone_name=args.timezone,
            environment=args.environment,
            acknowledge_machine_runtime=args.acknowledge_machine_runtime,
            acknowledge_capability_tour=args.acknowledge_capability_tour,
            acknowledge_schedule=args.acknowledge_schedule,
            process_kind=args.process_kind,
            process_interval_minutes=args.process_interval_minutes,
            process_time=args.process_time,
            process_every_days=args.process_every_days,
            morning_time=args.morning_time,
            daily_time=args.daily_time,
            weekly_weekday=args.weekly_weekday,
            weekly_time=args.weekly_time,
            maintenance_time=args.maintenance_time,
            recovery_interval_minutes=args.recovery_interval_minutes,
            log_retention_days=args.log_retention_days,
        )
    if args.operation == "disable":
        return disable(kb)
    if args.operation == "manage-reports":
        if args.enabled == "true":
            readiness = report_migration_readiness(kb)
            if not readiness["ready"]:
                raise DreamingError(
                    "DREAMING_REPORT_COVERAGE_UNSUPPORTED",
                    "仍有 Dreaming process 未支持的 routine 来源，拒绝迁移报告 owner。",
                    details=readiness,
                )
        return set_report_management(
            kb,
            enabled=args.enabled == "true",
            acknowledge_owner_released=args.acknowledge_owner_released,
        )
    if args.operation == "run-due":
        return run_due(
            kb,
            owner=args.owner,
            lease_seconds=args.lease_seconds,
        )
    if args.operation == "renew":
        return renew_lease(
            kb,
            token=args.token,
            lease_seconds=args.lease_seconds,
        )
    if args.operation == "heartbeat":
        return heartbeat_run(
            kb,
            token=args.token,
            stage=args.stage,
            detail_code=args.detail_code,
            progress_current=args.progress_current,
            progress_total=args.progress_total,
        )
    if args.operation == "retry-job":
        return retry_job(kb, job_name=args.job)
    if args.operation == "harness":
        if args.harness_operation == "register":
            return register_harness(kb, task_id=args.task_id)
        if args.harness_operation == "unregister":
            return unregister_harness(kb)
        raise AssertionError(args.harness_operation)
    if args.operation == "runs":
        if args.runs_operation == "list":
            return list_runs(kb, limit=args.limit)
        if args.runs_operation == "show":
            return show_run(kb, run_id=args.run_id)
        if args.runs_operation == "tail":
            return tail_events(
                kb,
                limit=args.limit,
                run_id=args.run_id,
            )
        raise AssertionError(args.runs_operation)
    if args.operation == "grant":
        if args.grant_operation == "set-actions":
            from dreaming_grants import set_action_grants

            return set_action_grants(
                kb,
                persist_report=args.persist_report,
                archive=args.archive,
                instant_alert=args.instant_alert,
            )
        return set_im_grant(
            kb,
            mode=args.mode,
            persist_finding=args.persist_finding,
            acknowledge_all_visible=args.acknowledge_all_visible,
        )
    if args.operation == "process":
        if args.process_operation == "prepare":
            return prepare_im_batch(
                kb,
                start=args.start,
                end=args.end,
            )
        if args.process_operation == "abort":
            return abort_batch(
                kb,
                batch_id=args.batch_id,
                error_code=args.error_code,
            )
        if args.process_operation == "commit":
            return commit_finding_bundle(
                kb,
                batch_id=args.batch_id,
                input_path=args.input,
                skill_root=ROOT,
                semantic_revision=args.semantic_revision,
            )
        if args.process_operation == "once":
            return prepare_foreground_im_batch(
                kb,
                start=args.start,
                end=args.end,
                mode=args.mode,
                acknowledge_all_visible=args.acknowledge_all_visible,
            )
        raise AssertionError(args.process_operation)
    if args.operation == "review":
        return review_findings(kb, status=args.status, limit=args.limit)
    if args.operation == "explain":
        return explain_finding(kb, finding_id=args.finding_id)
    if args.operation == "feedback":
        return record_finding_feedback(
            kb,
            finding_id=args.finding_id,
            status=args.status,
            value=args.value,
            request_id=args.request_id,
            snooze_until=args.snooze_until,
        )
    if args.operation == "shadow":
        return evaluate_shadow(
            kb=kb,
            skill_root=ROOT,
            evaluation_dir=args.evaluation_dir,
        )
    if args.operation == "report":
        if args.report_operation == "prepare":
            return prepare_report_packet(
                kb,
                kind=args.kind,
                period=args.period,
            )
        if args.report_operation == "render":
            return render_report_bundle(
                kb,
                document=load_report_document(args.input, skill_root=ROOT),
            )
        if args.report_operation == "enqueue-delivery":
            return enqueue_delivery(
                kb,
                kind=args.kind,
                period=args.period,
                report_path=args.report_path,
                commit=args.commit,
                channel=args.channel,
                artifact=args.artifact,
                recipient_id=args.recipient_id,
            )
        if args.report_operation == "deliver":
            return deliver_lark_bot_summary(kb, outbox_id=args.outbox_id)
        if args.report_operation == "delivery-complete":
            return complete_delivery(
                kb,
                outbox_id=args.outbox_id,
                delivery_id=args.delivery_id,
            )
        raise AssertionError(args.report_operation)
    if args.operation == "action":
        if args.action_operation == "plan":
            proposed = load_action_plan(args.input, skill_root=ROOT)
            evaluated = evaluate_action_plan(kb, plan=proposed)
            return plan_actions(
                kb,
                plan=evaluated,
                lease_token=args.lease_token,
            )
        if args.action_operation == "claim":
            return claim_action(
                kb,
                action_id=args.action_id,
                lease_token=args.lease_token,
                confirmed=args.confirmed,
            )
        if args.action_operation == "validate-claim":
            return validate_claim(
                kb,
                action_id=args.action_id,
                claim_token=args.claim_token,
            )
        if args.action_operation == "complete":
            return complete_action(
                kb,
                action_id=args.action_id,
                claim_token=args.claim_token,
                receipt=load_downstream_receipt(args.receipt, skill_root=ROOT),
            )
        if args.action_operation == "cancel":
            return cancel_action(
                kb,
                action_id=args.action_id,
                reason=args.reason,
            )
        if args.action_operation == "reconcile":
            receipt = (
                load_downstream_receipt(args.receipt, skill_root=ROOT)
                if args.receipt
                else None
            )
            return reconcile_action(
                kb,
                action_id=args.action_id,
                receipt=receipt,
            )
        raise AssertionError(args.action_operation)
    if args.operation == "complete":
        return complete_run(
            kb,
            token=args.token,
            run_status=args.run_status,
            artifact_path=args.artifact_path,
            coverage_checkpoint=args.coverage_checkpoint,
            error_code=args.error_code,
            item_count=args.item_count,
            finding_count=args.finding_count,
            gap_count=args.gap_count,
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
    except DreamingError as exc:
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
