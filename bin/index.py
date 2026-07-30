#!/usr/bin/env python3
"""Machine-readable INDEX maintenance entrypoint."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_MAINTENANCE_SCHEMA = "byteworker-index-maintenance/v1"


class IndexMaintenanceError(RuntimeError):
    def __init__(self, code: str, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint

    def as_dict(self) -> dict[str, str]:
        result = {"code": self.code, "message": str(self)}
        if self.hint:
            result["hint"] = self.hint
        return result


def configured_kb() -> str:
    config = ROOT / ".kbconfig"
    if not config.is_file():
        return ""
    return config.read_text(encoding="utf-8").splitlines()[0].strip()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="byteworker 可确定重建的 INDEX 维护入口",
    )
    sub = result.add_subparsers(dest="operation", required=True)
    rebuild = sub.add_parser("rebuild", help="从真相源重建 INDEX.md")
    rebuild.add_argument(
        "--kb",
        default=configured_kb(),
        help="知识库数据目录；默认读取 .kbconfig",
    )
    rebuild.add_argument(
        "--dry-run",
        action="store_true",
        help="只比较重建结果，不写 INDEX.md",
    )
    return result


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _invoke_rebuild(kb: Path, *, dry_run: bool) -> subprocess.CompletedProcess:
    args = [
        sys.executable,
        str(ROOT / "bin" / "rebuild_index.py"),
        str(kb),
    ]
    if dry_run:
        args.append("--dry-run")
    return subprocess.run(
        args,
        text=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def rebuild(kb: Path, *, dry_run: bool) -> dict[str, object]:
    kb = kb.expanduser().resolve()
    if not kb.is_dir() or not (kb / "knowledge").is_dir():
        raise IndexMaintenanceError(
            "INDEX_KB_INVALID",
            f"不是有效的 byteworker 知识库目录: {kb}",
            hint="显式传入包含 knowledge/ 的知识库数据目录。",
        )
    preview = _invoke_rebuild(kb, dry_run=True)
    if preview.returncode != 0:
        message = (
            preview.stderr.decode("utf-8", errors="replace").strip()
            or preview.stdout.decode("utf-8", errors="replace").strip()
            or "INDEX 预演失败"
        )
        raise IndexMaintenanceError("INDEX_REBUILD_FAILED", message)
    index_path = kb / "INDEX.md"
    previous = index_path.read_bytes() if index_path.is_file() else b""
    expected = preview.stdout
    changed = previous != expected
    if not dry_run and changed:
        applied = _invoke_rebuild(kb, dry_run=False)
        if applied.returncode != 0:
            message = (
                applied.stderr.decode("utf-8", errors="replace").strip()
                or applied.stdout.decode("utf-8", errors="replace").strip()
                or "INDEX 重建失败"
            )
            raise IndexMaintenanceError("INDEX_REBUILD_FAILED", message)
        actual = index_path.read_bytes()
        if actual != expected:
            raise IndexMaintenanceError(
                "INDEX_REBUILD_VERIFY_FAILED",
                "INDEX.md 写入结果与 dry-run 预演不一致",
            )
    return {
        "schema_version": INDEX_MAINTENANCE_SCHEMA,
        "status": (
            "would_change"
            if dry_run and changed
            else "rebuilt"
            if changed
            else "unchanged"
        ),
        "mode": "dry-run" if dry_run else "apply",
        "changed": changed,
        "index_path": str(index_path),
        "previous_hash": _sha256(previous),
        "content_hash": _sha256(expected),
        "journal_written": False,
        "git_commit_created": False,
    }


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if not args.kb:
            raise IndexMaintenanceError(
                "KB_CONFIG_MISSING",
                "未指定 --kb 且 .kbconfig 不存在",
                hint="先完成知识库初始化，或显式传入 --kb。",
            )
        result = rebuild(Path(args.kb), dry_run=args.dry_run)
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    except IndexMaintenanceError as exc:
        print(
            json.dumps(
                {"error": exc.as_dict()},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
