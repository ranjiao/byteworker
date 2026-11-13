"""Bounded change checks using the public source and Digest facades."""

from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
from pathlib import Path
from typing import Any

from dreaming_digest import link_routine_digest, prepare_routine_digest, record_routine_digest
from dreaming_run_log import MACHINE_CODE
from dreaming_state import DreamingError
from sources import SourceBundleError, load_source_bundle


def _invoke(skill_root: Path, *arguments: str) -> dict[str, Any]:
    try:
        process = subprocess.run(
            [str(skill_root / "bin/byteworker"), *arguments],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=600, check=False,
        )
        response = json.loads(process.stdout)
    except subprocess.TimeoutExpired as exc:
        raise DreamingError("DREAMING_SOURCE_CHECK_TIMEOUT", "来源检查超时。") from exc
    except (OSError, ValueError) as exc:
        raise DreamingError("DREAMING_SOURCE_CHECK_FAILED", "无法读取来源检查回执。") from exc
    data = response.get("data", response) if isinstance(response, dict) else None
    if process.returncode or not isinstance(data, dict) or response.get("error"):
        error = response.get("error") if isinstance(response, dict) else None
        code = str(error.get("code", "")) if isinstance(error, dict) else ""
        message = error.get("message") if isinstance(error, dict) else None
        raise DreamingError(
            code if MACHINE_CODE.fullmatch(code) else "DREAMING_SOURCE_CHECK_FAILED",
            message if isinstance(message, str) and message else "来源检查命令失败。",
        )
    return data


def _check_source(kb, token, source, invoke):
    scope = ("--kb", str(kb))
    source_type = source["source_type"]
    source_uid = source["source_uid"]
    run_id = source.get("work", {}).get("digest_run_id")
    if run_id:
        flow = invoke("digest-flow", "status", *scope, "--run-id", run_id)
        if flow["phase"] == "failed" or flow.get("run", {}).get("status") == "stale":
            flow = invoke("digest-flow", "resume", *scope, "--run-id", run_id)
        if flow["phase"] in ("checked", "prepared", "commit_failed", "noop", "committed"):
            return flow
    else:
        flow = invoke("digest-flow", "start", *scope, "--source-type", source_type, "--source-ref", source_uid)
        run_id = flow["run_id"]
        link_routine_digest(kb, token=token, source_key=source["source_key"], digest_run_id=run_id)
    run_scope = (*scope, "--run-id", run_id)
    capture_stage = ("digest-run", "stage", *run_scope, "--stage", "capture", "--status")
    work = Path(flow["work_dir"])
    bundle_path = work / "capture-bundle.json"
    output = ("--bundle-only", "--out", str(bundle_path))
    started = "capture" in flow.get("run", {}).get("open_stages", [])
    try:
        if not bundle_path.is_file():
            if not started:
                invoke(*capture_stage, "started")
                started = True
            invoke("source", "capture", *scope, "--source-type", source_type, "--source-uid", source_uid, *output)
        bundle = load_source_bundle(bundle_path)
        profile = bundle.provider_metadata.get("source_profile", {})
        if (
            bundle.identity.source_uid != source_uid or bundle.identity.source_type != source_type
            or profile.get("revision") != source["profile_revision"]
        ):
            raise DreamingError("DREAMING_DIGEST_INVENTORY_CHANGED", "抓取期间来源或配置发生变化。")
        if started:
            invoke(*capture_stage, "completed")
        return invoke("digest-flow", "prepare", *run_scope, "--bundle", str(bundle_path), "--routine", "--check-only")
    except (DreamingError, SourceBundleError) as exc:
        try:
            invoke("digest-flow", "fail", *run_scope, "--error-code", exc.code)
        except DreamingError:
            pass
        raise DreamingError(exc.code, "来源检查失败。") from exc


def prepare_manual_source(kb: Path, *, token: str, source_key: str, skill_root: Path,
                          bundle: Path | None = None, bundle_request: Path | None = None) -> dict[str, Any]:
    batch = prepare_routine_digest(kb, token=token)
    source = next((row for row in batch["sources"] if row["source_key"] == source_key), None)
    if source is None or source["workflow"] == "wiki_scan_then_digest":
        raise DreamingError("DREAMING_DIGEST_RESULT_INVALID", "请选择本轮尚未完成的普通 Digest 来源。")
    if source["status"] != "pending":
        if source["status"] in {"noop", "empty_window", "committed"}:
            return {"phase": "committed" if source["status"] == "committed" else "noop",
                    "receipt": source["receipt"], "source_key": source_key, "source_uid": source["source_uid"],
                    "run_id": source.get("work", {}).get("digest_run_id", ""), "next_action": "none"}
        raise DreamingError("DREAMING_DIGEST_RESULT_INVALID", "请选择本轮尚未完成的普通 Digest 来源。")
    scope = ("--kb", str(kb))
    run_id = source.get("work", {}).get("digest_run_id")
    if run_id:
        flow = _invoke(skill_root, "digest-flow", "status", *scope, "--run-id", run_id)
        if flow["phase"] == "failed" or flow.get("run", {}).get("status") == "stale":
            flow = _invoke(skill_root, "digest-flow", "resume", *scope, "--run-id", run_id)
    else:
        flow = _invoke(skill_root, "digest-flow", "start", *scope, "--source-type", source["source_type"],
                       "--source-ref", source["source_uid"])
        run_id = flow["run_id"]
        link_routine_digest(kb, token=token, source_key=source_key, digest_run_id=run_id)
    if (bundle or bundle_request) and flow["phase"] not in {"checked", "prepared", "commit_failed", "noop", "committed", "cancelled"}:
        flag, path = ("--bundle", bundle) if bundle else ("--bundle-request", bundle_request)
        work_dir = flow["work_dir"]
        flow = _invoke(skill_root, "digest-flow", "prepare", *scope, "--run-id", run_id,
                       "--routine", "--check-only", "--source-uid", source["source_uid"], flag, str(path))
        flow["work_dir"] = work_dir
    receipt = flow.get("receipt")
    if receipt and receipt.get("status") in {"noop", "empty_window", "committed"}:
        record_routine_digest(kb, token=token, result={**source, "status": receipt["status"], "receipt": receipt})
    return {**flow, "source_key": source_key, "source_uid": source["source_uid"]}


def scan_routine_digest(kb: Path, *, token: str, skill_root: Path, max_workers: int = 3) -> dict[str, Any]:
    if max_workers not in (1, 2, 3):
        raise DreamingError("DREAMING_SOURCE_CHECK_FAILED", "来源检查并发必须是 1..3。")
    batch = prepare_routine_digest(kb, token=token)
    invoke = partial(_invoke, skill_root)
    pending = [source for source in batch["sources"] if source["status"] == "pending"]
    checks = {}
    if pending:
        capture = invoke("source", "capabilities")["operation_contracts"]["capture"]
        automatic = [
            source for source in pending
            if source["origin"] == "profile" and source["source_type"] in capture["source_types"]
        ]
        workers = max_workers
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            while automatic:
                wave, automatic = automatic[:workers], automatic[workers:]
                futures = {
                    pool.submit(_check_source, kb, token, source, invoke): source
                    for source in wave
                }
                for future in as_completed(futures):
                    source = futures[future]
                    key = source["source_key"]
                    try:
                        flow = future.result()
                        receipt = flow.get("receipt")
                        check = (
                            {"status": receipt["status"], "receipt": receipt}
                            if receipt else {"status": "changed", "digest_run_id": flow["run_id"],
                                             "preflight_state": flow["preflight_state"]}
                        )
                        if check["status"] != "changed":
                            record_routine_digest(kb, token=token, result={**source, **check})
                    except DreamingError as exc:
                        if exc.code == "DREAMING_LEASE_MISMATCH":
                            raise
                        if "RATE_LIMIT" in exc.code or "THROTTL" in exc.code:
                            workers = 1
                        check = {"status": "failed", "error_code": exc.code}
                        record_routine_digest(kb, token=token, result={**source, **check})
                    checks[key] = check

    batch = prepare_routine_digest(kb, token=token)
    for source in batch["sources"]:
        check = checks.get(source["source_key"])
        if check:
            source["check"] = {key: value for key, value in check.items() if key != "receipt"}
    return batch
