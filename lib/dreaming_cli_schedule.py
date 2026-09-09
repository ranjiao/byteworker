"""Dreaming lifecycle, schedule, harness, run-log, and completion CLI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dreaming_cli_common import optional_bool, positive
from dreaming_reports import report_migration_readiness
from dreaming_run_log import list_runs, show_run, tail_events
from dreaming_scheduler import (
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
from lark_recipient import resolve_lark_recipient


def register_commands(sub: argparse._SubParsersAction) -> None:
    status_parser = sub.add_parser("status", help="Show Dreaming configuration and runtime status")
    status_parser.add_argument("--kb", required=True, type=Path)
    status_parser.set_defaults(_handler=_handle_status)

    configure_parser = sub.add_parser("configure", help="Update Dreaming settings")
    configure_parser.add_argument("--kb", required=True, type=Path)
    configure_parser.add_argument("--timezone")
    configure_parser.add_argument("--process-kind", choices=("interval", "daily_time", "every_n_days"))
    configure_parser.add_argument("--process-interval-minutes", type=positive)
    configure_parser.add_argument("--process-time")
    configure_parser.add_argument("--process-every-days", type=positive)
    configure_parser.add_argument("--process-enabled", type=optional_bool)
    configure_parser.add_argument("--morning-time")
    configure_parser.add_argument("--morning-enabled", type=optional_bool)
    configure_parser.add_argument("--maintenance-time")
    configure_parser.add_argument("--maintenance-enabled", type=optional_bool)
    configure_parser.add_argument("--recovery-interval-minutes", type=positive)
    configure_parser.add_argument("--recovery-enabled", type=optional_bool)
    configure_parser.add_argument("--log-retention-days", type=positive)
    configure_parser.add_argument("--lark-delivery-enabled", type=optional_bool)
    recipient_group = configure_parser.add_mutually_exclusive_group()
    recipient_group.add_argument("--lark-recipient", help="飞书字母用户名（如 ranjiao）或 ou_ 开头的 open_id")
    recipient_group.add_argument("--lark-recipient-id", help="兼容旧调用；接受字母用户名或 ou_ 开头的 open_id")
    configure_parser.set_defaults(_handler=_handle_configure)

    enable_parser = sub.add_parser("enable", help="Enable explicitly acknowledged Dreaming schedules")
    enable_parser.add_argument("--kb", required=True, type=Path)
    enable_parser.add_argument("--harness", required=True)
    enable_parser.add_argument("--timezone", required=True)
    enable_parser.add_argument("--environment", choices=("local",), default="local")
    enable_parser.add_argument("--acknowledge-capability-tour", action="store_true", help="确认已向用户完整介绍 Dreaming 能力、授权、成本和边界")
    enable_parser.add_argument("--acknowledge-schedule", action="store_true", help="确认已向用户展示并由用户确认完整运行计划")
    enable_parser.add_argument("--acknowledge-machine-runtime", action="store_true", help="确认机器需保持开机、唤醒、联网，且 Dreaming 会产生额外开销")
    enable_parser.add_argument("--process-kind", choices=("interval", "daily_time", "every_n_days"))
    enable_parser.add_argument("--process-interval-minutes", type=positive)
    enable_parser.add_argument("--process-time")
    enable_parser.add_argument("--process-every-days", type=positive)
    enable_parser.add_argument("--morning-time")
    enable_parser.add_argument("--daily-time")
    enable_parser.add_argument("--weekly-weekday", type=int, choices=range(7))
    enable_parser.add_argument("--weekly-time")
    enable_parser.add_argument("--maintenance-time")
    enable_parser.add_argument("--recovery-interval-minutes", type=positive)
    enable_parser.add_argument("--log-retention-days", type=positive)
    enable_parser.set_defaults(_handler=_handle_enable)

    disable_parser = sub.add_parser("disable", help="Disable Dreaming")
    disable_parser.add_argument("--kb", required=True, type=Path)
    disable_parser.set_defaults(_handler=lambda args, kb, root: disable(kb))

    reports = sub.add_parser("manage-reports", help="Transfer periodic report ownership")
    reports.add_argument("--kb", required=True, type=Path)
    reports.add_argument("--enabled", choices=("true", "false"), required=True)
    reports.add_argument("--acknowledge-owner-released", action="store_true", help="确认旧日报/周报 scheduler owner 已释放")
    reports.set_defaults(_handler=_handle_report_management)

    due = sub.add_parser("run-due", help="Claim the next due Dreaming job")
    due.add_argument("--kb", required=True, type=Path)
    due.add_argument("--owner", required=True)
    due.add_argument("--lease-seconds", type=positive, default=7200)
    due.add_argument("--followup-after-run-id", default="", help="仅在刚完成 process catchup 后领取被解锁的报告 job")
    due.set_defaults(_handler=lambda args, kb, root: run_due(kb, owner=args.owner, lease_seconds=args.lease_seconds, followup_after_run_id=args.followup_after_run_id))

    renew = sub.add_parser("renew", help="Renew an active run lease")
    renew.add_argument("--kb", required=True, type=Path)
    renew.add_argument("--token", required=True)
    renew.add_argument("--lease-seconds", type=positive, default=7200)
    renew.set_defaults(_handler=lambda args, kb, root: renew_lease(kb, token=args.token, lease_seconds=args.lease_seconds))

    retry = sub.add_parser("retry-job", help="Retry an eligible failed job")
    retry.add_argument("--kb", required=True, type=Path)
    retry.add_argument("--job", required=True)
    retry.set_defaults(_handler=lambda args, kb, root: retry_job(kb, job_name=args.job))

    harness = sub.add_parser("harness", help="Manage the host scheduler registration")
    harness_sub = harness.add_subparsers(dest="harness_operation", required=True)
    harness_register = harness_sub.add_parser("register", help="Record host task registration")
    harness_register.add_argument("--kb", required=True, type=Path)
    harness_register.add_argument("--task-id", required=True)
    harness_register.set_defaults(_handler=lambda args, kb, root: register_harness(kb, task_id=args.task_id))
    harness_unregister = harness_sub.add_parser("unregister", help="Remove host task registration")
    harness_unregister.add_argument("--kb", required=True, type=Path)
    harness_unregister.set_defaults(_handler=lambda args, kb, root: unregister_harness(kb))

    heartbeat = sub.add_parser("heartbeat", help="Record bounded run progress")
    heartbeat.add_argument("--kb", required=True, type=Path)
    heartbeat.add_argument("--token", required=True)
    heartbeat.add_argument("--stage", required=True, choices=("scheduled", "collection", "analysis", "consolidation", "action", "report", "maintenance", "recovery", "complete"))
    heartbeat.add_argument("--detail-code", default="")
    heartbeat.add_argument("--progress-current", type=int)
    heartbeat.add_argument("--progress-total", type=int)
    heartbeat.set_defaults(_handler=lambda args, kb, root: heartbeat_run(kb, token=args.token, stage=args.stage, detail_code=args.detail_code, progress_current=args.progress_current, progress_total=args.progress_total))

    runs = sub.add_parser("runs", help="Inspect bounded Dreaming run logs")
    runs_sub = runs.add_subparsers(dest="runs_operation", required=True)
    runs_list = runs_sub.add_parser("list", help="List recent runs")
    runs_list.add_argument("--kb", required=True, type=Path)
    runs_list.add_argument("--limit", type=positive, default=50)
    runs_list.set_defaults(_handler=lambda args, kb, root: list_runs(kb, limit=args.limit))
    runs_show = runs_sub.add_parser("show", help="Show one run")
    runs_show.add_argument("--kb", required=True, type=Path)
    runs_show.add_argument("run_id")
    runs_show.set_defaults(_handler=lambda args, kb, root: show_run(kb, run_id=args.run_id))
    runs_tail = runs_sub.add_parser("tail", help="Tail recent run events")
    runs_tail.add_argument("--kb", required=True, type=Path)
    runs_tail.add_argument("--limit", type=positive, default=50)
    runs_tail.add_argument("--run-id", default="")
    runs_tail.set_defaults(_handler=lambda args, kb, root: tail_events(kb, limit=args.limit, run_id=args.run_id))

    complete = sub.add_parser("complete", help="Complete a leased Dreaming run")
    complete.add_argument("--kb", required=True, type=Path)
    complete.add_argument("--token", required=True)
    complete.add_argument("--run-status", required=True, choices=("success", "partial", "failed"))
    complete.add_argument("--artifact-path", default="")
    complete.add_argument("--coverage-checkpoint", default="")
    complete.add_argument("--error-code", default="")
    complete.add_argument("--item-count", type=int)
    complete.add_argument("--finding-count", type=int)
    complete.add_argument("--gap-count", type=int)
    complete.add_argument("--batch-id", default="")
    complete.add_argument("--result-input", type=Path)
    complete.set_defaults(_handler=_handle_complete)


def _handle_status(args, kb: Path, root: Path):
    return status(kb)


def _handle_configure(args, kb: Path, root: Path):
    recipient_input = args.lark_recipient if args.lark_recipient is not None else args.lark_recipient_id
    resolved = resolve_lark_recipient(recipient_input) if recipient_input is not None else None
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
        lark_recipient_id=resolved["recipient_id"] if resolved else None,
        lark_recipient_key=resolved["recipient_key"] if resolved else None,
    )


def _handle_enable(args, kb: Path, root: Path):
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


def _handle_report_management(args, kb: Path, root: Path):
    if args.enabled == "true":
        readiness = report_migration_readiness(kb)
        if not readiness["ready"]:
            raise DreamingError(
                "DREAMING_REPORT_COVERAGE_UNSUPPORTED",
                "定期来源清单无效，拒绝迁移报告 owner。",
                details=readiness,
            )
    return set_report_management(
        kb,
        enabled=args.enabled == "true",
        acknowledge_owner_released=args.acknowledge_owner_released,
    )


def _handle_complete(args, kb: Path, root: Path):
    result_document = None
    if args.result_input is not None:
        try:
            if args.result_input.stat().st_size > 1024 * 1024:
                raise DreamingError("DREAMING_RUN_RESULT_INVALID", "运行结果文档超过 1 MiB。")
            loaded = json.loads(args.result_input.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DreamingError("DREAMING_RUN_RESULT_INVALID", "无法读取运行结果文档。") from exc
        if not isinstance(loaded, dict):
            raise DreamingError("DREAMING_RUN_RESULT_INVALID", "运行结果文档必须是 JSON object。")
        result_document = loaded
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
        batch_id=args.batch_id,
        result_document=result_document,
    )
