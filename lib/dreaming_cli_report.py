"""Dreaming report preparation, rendering, completion, and delivery CLI."""

from __future__ import annotations

import argparse
from pathlib import Path

from dreaming_delivery_lark import deliver_lark_bot_summary
from dreaming_report_bundle import load_report_document, render_report_bundle
from dreaming_report_completion import complete_report_run
from dreaming_reports import complete_delivery, enqueue_delivery, prepare_report_packet


def register_commands(sub: argparse._SubParsersAction) -> None:
    report = sub.add_parser("report", help="Prepare, render, complete, or deliver reports")
    report_sub = report.add_subparsers(dest="report_operation", required=True)
    prepare = report_sub.add_parser("prepare", help="Prepare a bounded report packet")
    prepare.add_argument("--kb", required=True, type=Path)
    prepare.add_argument("--kind", required=True, choices=("morning", "daily", "weekly"))
    prepare.add_argument("--period", required=True)
    prepare.set_defaults(_handler=lambda args, kb, root: prepare_report_packet(kb, kind=args.kind, period=args.period))
    render = report_sub.add_parser("render", help="Render report artifacts from a document")
    render.add_argument("--kb", required=True, type=Path)
    render.add_argument("--input", required=True, type=Path)
    render.set_defaults(_handler=lambda args, kb, root: render_report_bundle(kb, document=load_report_document(args.input, skill_root=root)))
    complete = report_sub.add_parser("complete", help="Commit a report and complete its leased run")
    complete.add_argument("--kb", required=True, type=Path)
    complete.add_argument("--token", required=True)
    complete.add_argument("--input", required=True, type=Path)
    complete.add_argument("--item-count", type=int)
    complete.add_argument("--finding-count", type=int)
    complete.add_argument("--gap-count", type=int)
    complete.add_argument("--delivery-binary")
    complete.set_defaults(_handler=_complete)
    enqueue = report_sub.add_parser("enqueue-delivery", help="Queue a report delivery")
    enqueue.add_argument("--kb", required=True, type=Path)
    enqueue.add_argument("--kind", required=True, choices=("morning", "daily", "weekly"))
    enqueue.add_argument("--period", required=True)
    enqueue.add_argument("--report-path", required=True)
    enqueue.add_argument("--commit", required=True)
    enqueue.add_argument("--channel", choices=("host", "lark_bot"), default="host")
    enqueue.add_argument("--artifact", choices=("summary", "html", "markdown"), default="markdown")
    enqueue.add_argument("--recipient-id", default="")
    enqueue.set_defaults(_handler=_enqueue)
    deliver = report_sub.add_parser("deliver", help="Deliver one queued Lark bot summary")
    deliver.add_argument("--kb", required=True, type=Path)
    deliver.add_argument("--outbox-id", required=True)
    deliver.set_defaults(_handler=lambda args, kb, root: deliver_lark_bot_summary(kb, outbox_id=args.outbox_id))
    delivered = report_sub.add_parser("delivery-complete", help="Acknowledge host delivery")
    delivered.add_argument("--kb", required=True, type=Path)
    delivered.add_argument("--outbox-id", required=True)
    delivered.add_argument("--delivery-id", required=True)
    delivered.set_defaults(_handler=lambda args, kb, root: complete_delivery(kb, outbox_id=args.outbox_id, delivery_id=args.delivery_id))


def _complete(args, kb: Path, root: Path):
    return complete_report_run(
        kb,
        token=args.token,
        document=load_report_document(args.input, skill_root=root),
        item_count=args.item_count,
        finding_count=args.finding_count,
        gap_count=args.gap_count,
        delivery_binary=args.delivery_binary,
    )


def _enqueue(args, kb: Path, root: Path):
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
