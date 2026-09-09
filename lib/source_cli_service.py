"""Application service behind the declarative Source CLI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from snapshot_store import diff_current_against_kb
from source_bundle_request import build_bundle_from_request
from source_capture import SourceCaptureError, diff_captures, read_capture, write_capture, write_capture_pair
from source_operations import run_source_operation, source_operation_manifest, source_operation_types
from source_profiles import (
    PROFILE_SOURCE_TYPES,
    SourceProfileError,
    list_profiles,
    load_profile,
    profile_relative_path,
    profile_revision,
    save_profile,
)
from sources import BUNDLE_SCHEMA, create_default_registry


def run(args: argparse.Namespace, *, skill_root: Path) -> dict:
    if args.operation == "capabilities":
        return {
            "operation_source_types": list(source_operation_types()),
            "operation_contracts": source_operation_manifest(),
            "profile_source_types": sorted(PROFILE_SOURCE_TYPES),
            "bundle_source_types": list(create_default_registry().source_types()),
            "contract": BUNDLE_SCHEMA,
        }
    if args.operation == "bundle-spec":
        return create_default_registry().request_spec(args.source_type)
    if args.operation == "bundle":
        return build_bundle_from_request(
            args.source_type,
            Path(args.request),
            skill_root=skill_root,
        ).to_dict()
    if args.operation == "capture" and args.bundle_out and not args.out:
        raise SourceCaptureError("SOURCE_ARGUMENT_INVALID", "--bundle-out 必须与 --out 一起使用")
    if args.operation == "diff":
        return _diff(args)
    if args.operation == "profile":
        profile = load_profile(Path(args.kb).expanduser(), args.source_uid)
        return {
            "profile": profile,
            "profile_path": str(profile_relative_path(profile)),
            "profile_revision": profile_revision(profile),
        }
    if args.operation == "profile-save":
        return _save_profile(args, skill_root=skill_root)
    if args.operation == "profiles":
        profiles = list_profiles(Path(args.kb).expanduser(), source_type=args.source_type)
        return {
            "profiles": [
                {
                    "source_uid": profile["source_uid"],
                    "source_type": profile["source_type"],
                    "title": profile["title"],
                    "routine": profile["routine"],
                    "profile_path": str(profile_relative_path(profile)),
                    "profile_revision": profile_revision(profile),
                }
                for profile in profiles
            ],
            "count": len(profiles),
        }
    return run_source_operation(args, skill_root=skill_root)


def persist_result(
    args: argparse.Namespace,
    result: dict,
    *,
    skill_root: Path,
) -> dict:
    if args.operation == "bundle":
        output = Path(args.out).expanduser().resolve()
        write_capture(output, result, skill_root=skill_root)
        return _bundle_receipt(result, output)
    if args.operation not in {"capture", "diff"} or not args.out:
        return result

    output = Path(args.out).expanduser().resolve()
    if args.operation == "diff":
        write_capture(output, result, skill_root=skill_root)
        return {
            "schema_version": result["schema_version"],
            "source_type": result["source_type"],
            "source_uid": result["source_uid"],
            "output": str(output),
            "diff_hash": result["diff_hash"],
            "summary": result["summary"],
        }
    if result.get("schema_version") == BUNDLE_SCHEMA:
        if args.bundle_out:
            raise SourceCaptureError(
                "SOURCE_ARGUMENT_INVALID",
                "该来源 capture 已直接输出 SourceBundle，不得再提供 --bundle-out",
            )
        write_capture(output, result, skill_root=skill_root)
        return _bundle_receipt(result, output)

    bundle_output = ""
    if args.bundle_out:
        bundle_path = Path(args.bundle_out).expanduser().resolve()
        bundle = create_default_registry().build_bundle(
            args.source_type,
            capture=result,
            capture_path=output,
            skill_root=skill_root,
        )
        write_capture_pair(
            output,
            result,
            bundle_path,
            bundle.to_dict(),
            skill_root=skill_root,
        )
        bundle_output = str(bundle_path)
    else:
        write_capture(output, result, skill_root=skill_root)
    return {
        "schema_version": result["schema_version"],
        "source_type": result["source_type"],
        "source_uid": result["source_uid"],
        "title": result["title"],
        "output": str(output),
        "content_hash": result["content_hash"],
        "item_count": result["pagination"]["item_count"],
        "complete": result["pagination"]["complete"],
        "sanitization": result.get("sanitization", {}),
        **(
            {"bundle_schema_version": BUNDLE_SCHEMA, "bundle_output": bundle_output}
            if bundle_output
            else {}
        ),
    }


def _bundle_receipt(result: dict, output: Path) -> dict:
    return {
        "schema_version": result["schema_version"],
        "source_type": result["identity"]["source_type"],
        "source_uid": result["identity"]["source_uid"],
        "title": result["identity"]["title"],
        "output": str(output),
        "component_count": len(result["components"]),
        "coverage": result["coverage"]["status"],
    }


def _diff(args: argparse.Namespace) -> dict:
    current = read_capture(Path(args.current))
    if args.kb:
        if args.previous:
            raise SourceCaptureError(
                "SOURCE_ARGUMENT_INVALID",
                "--kb 与 --previous 不能同时使用",
                hint="让 SnapshotStore 从 KB raw 选择上一版本，或显式提供 capture 文件。",
            )
        return diff_current_against_kb(
            current,
            Path(args.kb),
            source_uid=args.source_uid or None,
            raw_id=args.raw_id or None,
            history_index=args.history_index,
        )
    if args.source_uid or args.raw_id or args.history_index:
        raise SourceCaptureError(
            "SOURCE_ARGUMENT_INVALID",
            "--source-uid / --raw-id / --history-index 必须和 --kb 一起使用",
        )
    previous = read_capture(Path(args.previous)) if args.previous else None
    return diff_captures(current=current, previous=previous)


def _save_profile(args: argparse.Namespace, *, skill_root: Path) -> dict:
    profile_path = Path(args.file).expanduser().resolve()
    root = skill_root.resolve()
    if root == profile_path or root in profile_path.parents:
        raise SourceProfileError(
            "SOURCE_PROFILE_IN_SKILL_REPO",
            "来源实例 profile 不得放在 byteworker skill 仓库",
        )
    try:
        value = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceProfileError(
            "SOURCE_PROFILE_INVALID",
            f"无法读取 source profile JSON: {profile_path}",
        ) from exc
    if not isinstance(value, dict):
        raise SourceProfileError("SOURCE_PROFILE_INVALID", "source profile 顶层必须是 JSON 对象")
    return save_profile(Path(args.kb).expanduser(), value, skill_root=skill_root)
