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
    DigestTxnError,
    batch_validation_report,
    execute_batch_plan,
    execute_plan,
    load_manifest,
    preflight,
    sha256_file,
    validate_batch_plan,
    validate_plan,
    validation_report,
)
from frontmatter import parse_file  # noqa: E402


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
        sources.append(manifest["source"])
    if isinstance(manifest.get("inputs"), list):
        sources.extend(
            item.get("source", {})
            for item in manifest["inputs"]
            if isinstance(item, dict) and isinstance(item.get("source"), dict)
        )
    for source in sources:
        for component in source.get("components", []):
            if not isinstance(component, dict) or not component.get("path"):
                continue
            component_path = Path(str(component["path"]))
            if not component_path.is_absolute():
                component_path = manifest_path.parent / component_path
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

        manifest_path = Path(args.manifest)
        manifest = load_manifest(manifest_path)
        reject_business_files_in_skill(manifest_path.resolve(), manifest)
        if args.command == "preflight":
            source = manifest.get("source", manifest)
            if not isinstance(source, dict):
                raise DigestTxnError("manifest.source 必须是对象")
            output = preflight(kb, source, manifest_path.resolve())
        elif args.command == "validate":
            if manifest.get("schema_version") == BATCH_PLAN_SCHEMA:
                output = batch_validation_report(validate_batch_plan(kb, manifest_path))
            else:
                output = validation_report(validate_plan(kb, manifest_path))
        else:
            if manifest.get("schema_version") == BATCH_PLAN_SCHEMA:
                output = execute_batch_plan(kb, manifest_path, ROOT)
            else:
                output = execute_plan(kb, manifest_path, ROOT)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except DigestTxnError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    except Exception as exc:  # defensive boundary: never emit a false success receipt
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
