#!/usr/bin/env python3
"""Stable launcher for Byteworker preflight, runtime tools, and machine CLI."""

from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from runtime_deps import (  # noqa: E402
    PYTHON_CACHE_FILENAME,
    RUNTIME_CACHE_FILENAME,
    cached_check_runtime,
    clear_runtime_cache,
    read_runtime_cache,
    render_dependency_report,
    runtime_environment,
)


def _exec(argv: list[str], env: dict[str, str]) -> int:
    try:
        os.execvpe(argv[0], argv, env)
    except OSError as exc:
        print(f"byteworker: 无法执行 {argv[0]}: {exc}", file=sys.stderr)
        return 1


def _runtime_command(name: str, args: list[str]) -> int:
    result, _cache = cached_check_runtime(
        ROOT,
        required_sources={name},
        include_optional=False,
    )
    if not result["ready"]:
        print(render_dependency_report(result), file=sys.stderr)
        return 1
    executable_key = "lark-cli" if name == "feishu" else "meegle"
    executable = result["programs"][executable_key]["path"]
    return _exec([executable, *args], runtime_environment(result))


def _required_sources(values: list[str]) -> set[str]:
    if values[:1] == ["wiki"]:
        operation = values[1] if len(values) > 1 else ""
        return {"feishu"} if operation in {"auth-status", "inspect", "scan"} else set()
    if values[:1] != ["source"]:
        return set()
    operation = values[1] if len(values) > 1 else ""
    if operation not in {"auth-status", "inspect", "capture"}:
        return set()
    source_type = ""
    for index, value in enumerate(values):
        if value.startswith("--source-type="):
            source_type = value.split("=", 1)[1]
            break
        if value == "--source-type" and index + 1 < len(values):
            source_type = values[index + 1]
            break
    if not source_type:
        return set()
    if source_type.startswith(("feishu_", "lark_")):
        return {"feishu"}
    if source_type in {"meego", "meegle"}:
        return {"meego"}
    return set()


def _deps_command(args: list[str]) -> int:
    refresh = False
    show_status = False
    positional: list[str] = []
    for arg in args:
        if arg == "--refresh":
            refresh = True
        elif arg == "--cache-status":
            show_status = True
        else:
            positional.append(arg)
    if positional:
        print(
            "byteworker: deps 仅接受 --refresh / --cache-status 参数。",
            file=sys.stderr,
        )
        return 2
    if show_status:
        cached, reason = read_runtime_cache(ROOT)
        if cached is None:
            print(f"运行时缓存: 未命中 ({reason})")
            print("  有效期: 永久；仅在路径失效或显式刷新时重建")
            print(f"  文件: {ROOT / RUNTIME_CACHE_FILENAME}")
            print(f"  Python 文件: {ROOT / PYTHON_CACHE_FILENAME}")
        else:
            import datetime as _dt

            ts = float(cached.get("generated_at", 0))
            age = int(_dt.datetime.now().timestamp() - ts) if ts else 0
            print("运行时缓存: 命中")
            print(f"  生成时间: {_dt.datetime.fromtimestamp(ts).isoformat() if ts else 'n/a'} (距今 {age}s)")
            print("  有效期: 永久；仅在路径失效或显式刷新时重建")
            print(f"  Python: {cached.get('python', {}).get('path', '')}")
            print(f"  Python 版本: {cached.get('python', {}).get('version', '')}")
            print(f"  就绪状态: ready={cached.get('ready')}, core_ready={cached.get('core_ready')}")
            programs = cached.get("programs", {})
            for name in sorted(programs):
                p = programs[name]
                print(f"  {name}: status={p.get('status')} path={p.get('path', '')}")
        return 0
    result, cache_status = cached_check_runtime(
        ROOT,
        required_sources={"feishu", "meego"},
        include_optional=True,
        force_refresh=refresh,
    )
    print(render_dependency_report(result))
    print(f"[cache: {cache_status}]")
    if any(
        item["required"] and item["tier"] == 1 and item["status"] != "ok"
        for item in result["programs"].values()
    ):
        return 1
    return 2 if not result["ready"] else 0


def _runtime_reset_command() -> int:
    removed, message = clear_runtime_cache(ROOT)
    print(f"运行时缓存已重置: {message}")
    return 0


def main(argv: list[str] | None = None) -> int:
    values = sys.argv[1:] if argv is None else argv
    if not values:
        values = ["--help"]
    command, *rest = values

    if command == "preflight":
        return _exec(
            [
                os.environ.get("BYTEWORKER_PYTHON_BIN", sys.executable),
                str(ROOT / "bin" / "session-preflight.py"),
                *rest,
            ],
            dict(os.environ),
        )
    if command == "deps":
        return _deps_command(rest)
    if command == "runtime-reset":
        return _runtime_reset_command()
    if command == "lark":
        return _runtime_command("feishu", rest)
    if command == "meegle":
        return _runtime_command("meego", rest)
    if command == "run":
        if not rest:
            print("byteworker: run 需要命令参数。", file=sys.stderr)
            return 2
        result, _cache = cached_check_runtime(ROOT, include_optional=True)
        if not result["ready"]:
            print(render_dependency_report(result), file=sys.stderr)
            return 1
        return _exec(rest, runtime_environment(result))

    required_sources = _required_sources(values)
    result, _cache = cached_check_runtime(
        ROOT,
        required_sources=required_sources,
        include_optional=False,
    )
    if not result["ready"]:
        print(render_dependency_report(result), file=sys.stderr)
        return 1
    return _exec(
        [
            os.environ.get("BYTEWORKER_PYTHON_BIN", sys.executable),
            str(ROOT / "bin" / "byteworker-cli.py"),
            *values,
        ],
        runtime_environment(result),
    )


if __name__ == "__main__":
    raise SystemExit(main())
