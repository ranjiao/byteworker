#!/usr/bin/env python3
"""byteworker digest transaction CLI."""

import argparse
import json
import sys
from pathlib import Path


SELF_DIR = Path(__file__).resolve().parent
ROOT = SELF_DIR.parent
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from digest_txn import (  # noqa: E402
    BATCH_PLAN_SCHEMA,
    BATCH_PLAN_SCHEMA_V2,
    DigestTxnError,
    batch_validation_report,
    execute_batch_plan,
    execute_plan,
    load_manifest,
    preflight,
    preflight_bundle,
    sha256_file,
    validate_batch_plan,
    validate_plan,
    validation_report,
)
from digest_run_log import DigestRunError, record_stage  # noqa: E402
from frontmatter import parse_file  # noqa: E402
from sources import BUNDLE_SCHEMA  # noqa: E402


def configured_kb() -> str:
    config = ROOT / ".kbconfig"
    if not config.is_file():
        return ""
    return config.read_text(encoding="utf-8").splitlines()[0].strip()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="byteworker 确定性 digest 写入事务",
    )
    sub = result.add_subparsers(dest="command", required=True)
    for name in ("preflight", "validate", "execute"):
        command = sub.add_parser(name)
        command.add_argument(
            "--kb",
            default=configured_kb(),
            help="知识库数据目录；默认读取 .kbconfig",
        )
        command.add_argument(
            "--manifest",
            "--source" if name == "preflight" else "--plan",
            dest="manifest",
            required=True,
            help="临时 JSON manifest / plan",
        )
        command.add_argument(
            "--run-id",
            default="",
            help="可选 digest-run id；自动记录本事务阶段耗时",
        )
    snapshot = sub.add_parser("snapshot-node")
    snapshot.add_argument(
        "--kb",
        default=configured_kb(),
        help="知识库数据目录；默认读取 .kbconfig",
    )
    snapshot.add_argument(
        "--path",
        required=True,
        help="knowledge/ 下的节点相对路径",
    )
    return result


def _run_stage(command: str) -> str:
    return {
        "preflight": "preflight",
        "validate": "transaction_validate",
        "execute": "transaction",
    }[command]


def _stage_metrics(output: dict) -> dict[str, int]:
    metrics: dict[str, int] = {}
    component_hashes = output.get("component_hashes")
    if isinstance(component_hashes, dict):
        metrics["component_count"] = len(component_hashes)
    raws = output.get("raws")
    if isinstance(raws, list):
        metrics["item_count"] = len(raws)
    elif isinstance(output.get("batch_size"), int):
        metrics["item_count"] = output["batch_size"]
    nodes = output.get("nodes")
    if isinstance(nodes, list):
        metrics["node_count"] = len(nodes)
    else:
        created = output.get("created")
        updated = output.get("updated")
        if isinstance(created, list) and isinstance(updated, list):
            metrics["node_count"] = len(created) + len(updated)
    warnings = output.get("warnings")
    if isinstance(warnings, list):
        metrics["warning_count"] = len(warnings)
    evidence_count = output.get("evidence_count")
    if isinstance(evidence_count, int) and evidence_count >= 0:
        metrics["evidence_count"] = evidence_count
    return metrics


def _record_failed_stage(kb: Path, args: argparse.Namespace) -> None:
    if not getattr(args, "run_id", ""):
        return
    try:
        record_stage(
            kb,
            run_id=args.run_id,
            stage=_run_stage(args.command),
            status="failed",
            detail_code=f"DIGEST_TXN_{args.command.upper()}_FAILED",
        )
    except Exception:
        pass


def reject_business_files_in_skill(manifest_path: Path, manifest: dict) -> None:
    def inside_skill(path: Path) -> bool:
        try:
            path.resolve().relative_to(ROOT)
            return True
        except ValueError:
            return False

    if inside_skill(manifest_path):
        raise DigestTxnError(
            "manifest 可能包含业务数据，必须放系统临时目录，不能放 skill 仓库"
        )
    sources = []
    if isinstance(manifest.get("source"), dict):
        sources.append((manifest["source"], manifest_path))
    if isinstance(manifest.get("inputs"), list):
        sources.extend(
            (item.get("source", {}), manifest_path)
            for item in manifest["inputs"]
            if isinstance(item, dict) and isinstance(item.get("source"), dict)
        )

    bundle_references = []
    if manifest.get("source_bundle"):
        bundle_references.append(manifest["source_bundle"])
    if isinstance(manifest.get("inputs"), list):
        bundle_references.extend(
            item.get("source_bundle")
            for item in manifest["inputs"]
            if isinstance(item, dict) and item.get("source_bundle")
        )
    if manifest.get("schema_version") == BUNDLE_SCHEMA:
        sources.append(({"components": manifest.get("components", [])}, manifest_path))
    for bundle_reference in bundle_references:
        bundle_path = Path(str(bundle_reference))
        if not bundle_path.is_absolute():
            bundle_path = manifest_path.parent / bundle_path
        bundle_path = bundle_path.resolve()
        if inside_skill(bundle_path):
            raise DigestTxnError(
                "source bundle 可能包含业务数据，必须放系统临时目录，不能放 skill 仓库"
            )
        try:
            bundle_manifest = json.loads(bundle_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DigestTxnError(f"无法读取 source bundle: {bundle_path}") from exc
        if bundle_manifest.get("schema_version") == BUNDLE_SCHEMA:
            sources.append(
                (
                    {"components": bundle_manifest.get("components", [])},
                    bundle_path,
                )
            )
    for source, source_manifest_path in sources:
        for component in source.get("components", []):
            if not isinstance(component, dict) or not component.get("path"):
                continue
            component_path = Path(str(component["path"]))
            if not component_path.is_absolute():
                component_path = source_manifest_path.parent / component_path
            if inside_skill(component_path):
                raise DigestTxnError(
                    "source component 可能包含业务数据，必须放系统临时目录，不能放 skill 仓库"
                )

    for node in manifest.get("nodes", []):
        if not isinstance(node, dict) or not node.get("candidate"):
            continue
        candidate = Path(str(node["candidate"]))
        if not candidate.is_absolute():
            candidate = manifest_path.parent / candidate
        if inside_skill(candidate):
            raise DigestTxnError(
                "候选节点可能包含业务数据，必须放系统临时目录，不能放 skill 仓库"
            )


def main() -> int:
    args = parser().parse_args()
    if not args.kb:
        print("错误: 未指定 --kb 且 .kbconfig 不存在", file=sys.stderr)
        return 2
    kb = Path(args.kb)
    stage_started = False
    try:
        if args.command == "snapshot-node":
            relative = Path(args.path)
            if relative.is_absolute() or ".." in relative.parts:
                raise DigestTxnError("snapshot-node --path 必须是知识库内相对路径")
            path = (kb.resolve() / relative).resolve()
            try:
                path.relative_to((kb.resolve() / "knowledge").resolve())
            except ValueError as exc:
                raise DigestTxnError("snapshot-node 只允许读取 knowledge/ 节点") from exc
            if not path.is_file():
                raise DigestTxnError(f"节点不存在: {relative}")
            fm, _ = parse_file(str(path))
            output = {
                "status": "ok",
                "path": str(relative),
                "id": str(fm.get("id", "")),
                "type": str(fm.get("type", "")),
                "base_sha256": sha256_file(path),
            }
            print(json.dumps(output, ensure_ascii=False, indent=2))
            return 0

        if args.run_id:
            record_stage(
                kb,
                run_id=args.run_id,
                stage=_run_stage(args.command),
                status="started",
                detail_code=f"DIGEST_TXN_{args.command.upper()}_STARTED",
            )
            stage_started = True

        manifest_path = Path(args.manifest)
        manifest = load_manifest(manifest_path)
        reject_business_files_in_skill(manifest_path.resolve(), manifest)
        if args.command == "preflight":
            if manifest.get("schema_version") == BUNDLE_SCHEMA:
                output = preflight_bundle(kb, manifest_path.resolve())
            elif manifest.get("source_bundle"):
                bundle_path = Path(str(manifest["source_bundle"]))
                if not bundle_path.is_absolute():
                    bundle_path = manifest_path.parent / bundle_path
                output = preflight_bundle(kb, bundle_path.resolve())
            else:
                source = manifest.get("source", manifest)
                if not isinstance(source, dict):
                    raise DigestTxnError("manifest.source 必须是对象")
                output = preflight(kb, source, manifest_path.resolve())
        elif args.command == "validate":
            if manifest.get("schema_version") in {
                BATCH_PLAN_SCHEMA,
                BATCH_PLAN_SCHEMA_V2,
            }:
                output = batch_validation_report(validate_batch_plan(kb, manifest_path))
            else:
                output = validation_report(validate_plan(kb, manifest_path))
        else:
            if manifest.get("schema_version") in {
                BATCH_PLAN_SCHEMA,
                BATCH_PLAN_SCHEMA_V2,
            }:
                output = execute_batch_plan(kb, manifest_path, ROOT)
            else:
                output = execute_plan(kb, manifest_path, ROOT)
        if args.run_id:
            try:
                record_stage(
                    kb,
                    run_id=args.run_id,
                    stage=_run_stage(args.command),
                    status="completed",
                    detail_code=f"DIGEST_TXN_{args.command.upper()}_COMPLETED",
                    metrics=_stage_metrics(output),
                )
                output["digest_run_id"] = args.run_id
                output["digest_run_logging"] = "ok"
            except Exception as exc:
                code = (
                    exc.code
                    if isinstance(exc, DigestRunError)
                    else "DIGEST_RUN_LOG_IO_ERROR"
                )
                warnings = output.setdefault("warnings", [])
                if isinstance(warnings, list):
                    warnings.append(
                        "digest-run completion logging degraded: " + code
                    )
                output["digest_run_id"] = args.run_id
                output["digest_run_logging"] = "degraded"
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except DigestTxnError as exc:
        if stage_started:
            _record_failed_stage(kb, args)
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    except DigestRunError as exc:
        print(
            json.dumps(
                {"status": "error", "error": exc.as_dict()},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    except Exception as exc:  # defensive boundary: never emit a false success receipt
        if stage_started:
            _record_failed_stage(kb, args)
        print(
            json.dumps(
                {"status": "error", "error": f"unexpected: {exc}"},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
