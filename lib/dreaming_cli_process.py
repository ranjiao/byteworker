"""Dreaming grant and deterministic processing CLI."""

from __future__ import annotations

import argparse
from pathlib import Path

from dreaming_batch import abort_batch
from dreaming_collection import prepare_foreground_im_batch, prepare_im_batch
from dreaming_digest import complete_routine_digest, prepare_routine_digest, record_routine_digest_result
from dreaming_grants import set_action_grants, set_im_grant
from dreaming_process import commit_finding_bundle


def register_commands(sub: argparse._SubParsersAction) -> None:
    grant = sub.add_parser("grant", help="Configure Dreaming grants")
    grant_sub = grant.add_subparsers(dest="grant_operation", required=True)
    set_im = grant_sub.add_parser("set-im", help="Configure IM visibility and persistence")
    set_im.add_argument("--kb", required=True, type=Path)
    set_im.add_argument("--mode", required=True, choices=("off", "monitored", "all_visible"))
    set_im.add_argument("--persist-finding", action="store_true")
    set_im.add_argument("--acknowledge-all-visible", action="store_true")
    set_im.set_defaults(_handler=_set_im)
    set_actions = grant_sub.add_parser("set-actions", help="Configure allowed action classes")
    set_actions.add_argument("--kb", required=True, type=Path)
    set_actions.add_argument("--persist-report", action="store_true")
    set_actions.add_argument("--archive", action="store_true")
    set_actions.add_argument("--instant-alert", action="store_true")
    set_actions.set_defaults(_handler=_set_actions)

    process = sub.add_parser("process", help="Prepare, commit, or abort Dreaming processing")
    process_sub = process.add_subparsers(dest="process_operation", required=True)
    digest_prepare = process_sub.add_parser("digest-prepare", help="Prepare the next routine digest batch")
    digest_prepare.add_argument("--kb", required=True, type=Path)
    digest_prepare.add_argument("--token", required=True)
    digest_prepare.set_defaults(_handler=lambda args, kb, root: prepare_routine_digest(kb, token=args.token))
    digest_record = process_sub.add_parser("digest-record", help="Record one routine digest result")
    digest_record.add_argument("--kb", required=True, type=Path)
    digest_record.add_argument("--token", required=True)
    digest_record.add_argument("--input", required=True, type=Path)
    digest_record.set_defaults(_handler=lambda args, kb, root: record_routine_digest_result(kb, token=args.token, input_path=args.input))
    digest_complete = process_sub.add_parser("digest-complete", help="Complete the routine digest batch")
    digest_complete.add_argument("--kb", required=True, type=Path)
    digest_complete.add_argument("--token", required=True)
    digest_complete.set_defaults(_handler=lambda args, kb, root: complete_routine_digest(kb, token=args.token))
    prepare = process_sub.add_parser("prepare", help="Prepare a background IM batch")
    prepare.add_argument("--kb", required=True, type=Path)
    prepare.add_argument("--source", choices=("im",), required=True)
    prepare.add_argument("--start", required=True)
    prepare.add_argument("--end", required=True)
    prepare.set_defaults(_handler=lambda args, kb, root: prepare_im_batch(kb, start=args.start, end=args.end))
    abort = process_sub.add_parser("abort", help="Abort a prepared batch")
    abort.add_argument("--kb", required=True, type=Path)
    abort.add_argument("--batch-id", required=True)
    abort.add_argument("--error-code", required=True)
    abort.set_defaults(_handler=lambda args, kb, root: abort_batch(kb, batch_id=args.batch_id, error_code=args.error_code))
    commit = process_sub.add_parser("commit", help="Commit a validated FindingBundle")
    commit.add_argument("--kb", required=True, type=Path)
    commit.add_argument("--batch-id", required=True)
    commit.add_argument("--input", required=True, type=Path)
    commit.add_argument("--semantic-revision", default="finding-v1")
    commit.set_defaults(_handler=_commit)
    once = process_sub.add_parser("once", help="Prepare one foreground IM batch")
    once.add_argument("--kb", required=True, type=Path)
    once.add_argument("--source", choices=("im",), required=True)
    once.add_argument("--mode", required=True, choices=("monitored", "all_visible"))
    once.add_argument("--start", required=True)
    once.add_argument("--end", required=True)
    once.add_argument("--acknowledge-all-visible", action="store_true")
    once.set_defaults(_handler=_once)


def _set_im(args, kb: Path, root: Path):
    return set_im_grant(
        kb,
        mode=args.mode,
        persist_finding=args.persist_finding,
        acknowledge_all_visible=args.acknowledge_all_visible,
    )


def _set_actions(args, kb: Path, root: Path):
    return set_action_grants(
        kb,
        persist_report=args.persist_report,
        archive=args.archive,
        instant_alert=args.instant_alert,
    )


def _commit(args, kb: Path, root: Path):
    return commit_finding_bundle(
        kb,
        batch_id=args.batch_id,
        input_path=args.input,
        skill_root=root,
        semantic_revision=args.semantic_revision,
    )


def _once(args, kb: Path, root: Path):
    return prepare_foreground_im_batch(
        kb,
        start=args.start,
        end=args.end,
        mode=args.mode,
        acknowledge_all_visible=args.acknowledge_all_visible,
    )
