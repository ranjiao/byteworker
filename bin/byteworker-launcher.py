#!/usr/bin/env python3
"""Stable launcher for Byteworker preflight, runtime tools, and machine CLI."""

from __future__ import annotations

import os
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from command_registry import (  # noqa: E402
    command_description,
    command_manifest,
    command_spec,
    render_command_help,
    render_namespace_help,
    render_top_level_help,
    resolve_command_alias,
    required_sources_for,
    search_commands,
)
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
    """Compatibility wrapper for callers that imported the old helper."""
    return required_sources_for(values)


def _write_json(value: object) -> None:
    json.dump(value, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")


def _commands_command(args: list[str]) -> int:
    include_hidden = "--all" in args
    json_output = "--json" in args
    positional = [value for value in args if value not in {"--all", "--json"}]
    if not positional:
        print(render_command_help(command_spec("commands")), end="")
        return 0
    operation, *rest = positional
    if operation == "list" and not rest:
        if json_output:
            _write_json(command_manifest(include_hidden=include_hidden))
        else:
            print(render_top_level_help(include_hidden=include_hidden), end="")
        return 0
    if operation == "describe" and len(rest) == 1:
        value = command_description(rest[0])
        if value is None:
            print(f"byteworker: 未知命令路径: {rest[0]}", file=sys.stderr)
            return 2
        if json_output:
            _write_json(value)
        else:
            command = value["command"]
            spec = command_spec(str(command["name"]))
            print(render_command_help(spec), end="")
            if command.get("command_path") != command["name"]:
                print(f"requested-path: {command['command_path']}")
                print(f"help-command: {command['help_command']}")
        return 0
    if operation == "search" and rest:
        query = " ".join(rest)
        value = search_commands(query, include_hidden=include_hidden)
        if json_output:
            _write_json(value)
        else:
            for item in value["commands"]:
                print(f"{item['name']:<24} {item['summary']}")
            for item in value["aliases"]:
                print(f"{item['argv']:<24} {item['summary']} -> {item['target']}")
            if not value["count"]:
                print("没有匹配命令。")
        return 0
    print(
        "byteworker: commands 用法为 list、describe <path> 或 search <query>。",
        file=sys.stderr,
    )
    return 2


def _help_requested(args: list[str]) -> bool:
    return any(value in {"-h", "--help"} for value in args)


def _render_wrapper_help(name: str) -> int:
    spec = command_spec(name)
    if spec is None:
        return 2
    print(render_command_help(spec), end="")
    return 0


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
    if not values or all(value in {"-h", "--help", "--all"} for value in values):
        print(render_top_level_help(include_hidden="--all" in values), end="")
        return 0

    raw_dispatch_values = values[1:] if values[:1] == ["--pretty"] else values
    if not raw_dispatch_values:
        print(render_top_level_help(), end="")
        return 0
    help_prefix = [
        value for value in raw_dispatch_values if value not in {"-h", "--help"}
    ]
    namespace_help = render_namespace_help(help_prefix)
    if (
        namespace_help is not None
        and command_spec(help_prefix[0]) is None
        and (
            _help_requested(raw_dispatch_values)
            or len(help_prefix) == len(raw_dispatch_values)
        )
        and resolve_command_alias(help_prefix) == help_prefix
    ):
        print(namespace_help, end="")
        return 0
    dispatch_values = resolve_command_alias(raw_dispatch_values)
    effective_values = (
        ["--pretty", *dispatch_values]
        if values[:1] == ["--pretty"]
        else dispatch_values
    )
    command, *rest = dispatch_values
    spec = command_spec(command)

    if _help_requested(rest):
        if spec is None:
            print(f"byteworker: 未知命令: {command}", file=sys.stderr)
            return 2
        if command == "preflight":
            return _exec(
                [
                    os.environ.get("BYTEWORKER_PYTHON_BIN", sys.executable),
                    str(ROOT / "bin" / "session-preflight.py"),
                    *rest,
                ],
                dict(os.environ),
            )
        if spec.execution == "facade" and spec.stability != "tombstone":
            return _exec(
                [
                    os.environ.get("BYTEWORKER_PYTHON_BIN", sys.executable),
                    str(ROOT / "bin" / spec.entrypoint),
                    *rest,
                ],
                dict(os.environ),
            )
        return _render_wrapper_help(command)

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
        if rest:
            print("byteworker: runtime-reset 不接受参数。", file=sys.stderr)
            return 2
        return _runtime_reset_command()
    if command == "commands":
        return _commands_command(rest)
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

    required_sources = required_sources_for(dispatch_values)
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
            *effective_values,
        ],
        runtime_environment(result),
    )


if __name__ == "__main__":
    raise SystemExit(main())
