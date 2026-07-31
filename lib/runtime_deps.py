"""Deterministic runtime discovery for Byteworker launchers and preflight."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Mapping


MIN_PYTHON = (3, 10)
CACHE_SCHEMA_VERSION = "byteworker-runtime-cache/v2"
PYTHON_CACHE_FILENAME = ".python-cache.txt"
RUNTIME_CACHE_FILENAME = ".runtime-cache.json"

PROGRAM_SPECS = {
    "git": {"tier": 1, "args": ["--version"]},
    "jq": {"tier": 1, "args": ["--version"]},
    "bash": {"tier": 1, "args": ["--version"]},
    "node": {"tier": 2, "args": ["--version"], "source": "feishu"},
    "lark-cli": {"tier": 2, "args": ["--version"], "source": "feishu"},
    # meegle currently has no stable --version flag; --help still proves that
    # its Node launcher and command module can load successfully.
    "meegle": {"tier": 2, "args": ["--help"], "source": "meego"},
}
EXPLICIT_ENV = {
    "lark-cli": "BYTEWORKER_LARK_CLI_BIN",
    "meegle": "BYTEWORKER_MEEGLE_BIN",
    "node": "BYTEWORKER_NODE_BIN",
}


def _version_key(path: Path) -> tuple[int, ...]:
    match = re.search(r"/v(\d+(?:\.\d+)*)/", path.as_posix())
    return tuple(int(item) for item in match.group(1).split(".")) if match else ()


def _candidate_dirs(home: Path, path_value: str) -> list[Path]:
    values = [Path(item).expanduser() for item in path_value.split(os.pathsep) if item]
    values.extend([home / ".local" / "bin", home / ".volta" / "bin"])
    nvm = home / ".nvm" / "versions" / "node"
    if nvm.is_dir():
        values.extend(
            sorted(
                (path / "bin" for path in nvm.iterdir() if (path / "bin").is_dir()),
                key=_version_key,
                reverse=True,
            )
        )
    values.extend(
        [
            Path("/opt/homebrew/bin"),
            Path("/usr/local/bin"),
            Path("/usr/bin"),
            Path("/bin"),
        ]
    )
    result: list[Path] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value)
        if normalized not in seen:
            seen.add(normalized)
            result.append(value)
    return result


def _resolve_program(
    name: str,
    *,
    environ: Mapping[str, str],
    home: Path,
) -> tuple[str, str]:
    explicit_name = EXPLICIT_ENV.get(name, "")
    explicit = environ.get(explicit_name, "").strip() if explicit_name else ""
    if explicit:
        candidate = Path(explicit).expanduser()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.absolute()), ""
        return "", f"{explicit_name} 指向不可执行文件: {candidate}"

    found = shutil.which(name, path=environ.get("PATH", ""))
    if found:
        return str(Path(found).absolute()), ""
    for directory in _candidate_dirs(home, environ.get("PATH", "")):
        candidate = directory / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.absolute()), ""
    return "", f"未找到 {name}"


def _bounded(value: str, limit: int = 240) -> str:
    normalized = " ".join(value.split())
    return normalized if len(normalized) <= limit else normalized[: limit - 1] + "…"


def _probe(
    path: str,
    args: list[str],
    *,
    environ: Mapping[str, str],
    runner=subprocess.run,
) -> tuple[bool, str, str]:
    try:
        completed = runner(
            [path, *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=8,
            env=dict(environ),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, "", _bounded(str(exc))
    output = _bounded(completed.stdout or completed.stderr)
    if completed.returncode != 0:
        return False, "", output or f"退出码 {completed.returncode}"
    return True, output, ""


def runtime_environment(
    result: Mapping[str, Any],
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    env = dict(os.environ if environ is None else environ)
    prefixes: list[str] = []
    programs = result.get("programs", {})
    preferred_order = [
        *(
            name
            for name in ("lark-cli", "meegle", "node")
            if name in programs
        ),
        *(name for name in programs if name not in {"lark-cli", "meegle", "node"}),
    ]
    for name in preferred_order:
        item = programs[name]
        path = item.get("path")
        if path and item.get("status") == "ok":
            prefixes.append(str(Path(path).parent))
    python_path = result.get("python", {}).get("path")
    if python_path:
        prefixes.append(str(Path(python_path).parent))
        env["BYTEWORKER_PYTHON_BIN"] = python_path
    for program, variable in EXPLICIT_ENV.items():
        item = result.get("programs", {}).get(program, {})
        path = item.get("path")
        if path and item.get("status") == "ok":
            env[variable] = path
    existing = [item for item in env.get("PATH", "").split(os.pathsep) if item]
    env["PATH"] = os.pathsep.join(dict.fromkeys([*prefixes, *existing]))
    return env


def check_runtime(
    *,
    required_sources: Iterable[str] = (),
    include_optional: bool = False,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
    python_executable: str | None = None,
    python_version: tuple[int, int, int] | None = None,
    runner=subprocess.run,
) -> dict[str, Any]:
    env = dict(os.environ if environ is None else environ)
    home_path = home or Path(env.get("HOME", str(Path.home())))
    required = set(required_sources)
    programs: dict[str, dict[str, Any]] = {}

    py_version = python_version or tuple(sys.version_info[:3])
    py_path = str(
        Path(
            python_executable
            or env.get("BYTEWORKER_PYTHON_BIN")
            or sys.executable
        ).expanduser().absolute()
    )
    py_ready = py_version >= MIN_PYTHON
    python = {
        "path": py_path,
        "version": ".".join(str(item) for item in py_version),
        "status": "ok" if py_ready else "missing",
        "required": True,
        "tier": 1,
        "error": "" if py_ready else "需要 Python >= 3.10 且包含 zoneinfo",
    }

    provisional = {"programs": programs, "python": python}
    for name, spec in PROGRAM_SPECS.items():
        source = spec.get("source")
        explicit_name = EXPLICIT_ENV.get(name, "")
        explicit_required = bool(
            explicit_name and env.get(explicit_name, "").strip()
        )
        is_required = (
            spec["tier"] == 1
            or source in required
            or explicit_required
        )
        if spec["tier"] == 2 and not (include_optional or is_required):
            continue
        path, resolution_error = _resolve_program(name, environ=env, home=home_path)
        item = {
            "path": path,
            "version": "",
            "status": "missing",
            "required": is_required,
            "tier": spec["tier"],
            "error": resolution_error,
        }
        programs[name] = item
        if not path:
            continue
        probe_env = runtime_environment(provisional, environ=env)
        ok, version, error = _probe(path, spec["args"], environ=probe_env, runner=runner)
        item.update(
            {
                "version": version,
                "status": "ok" if ok else "broken",
                "error": error,
            }
        )

    core_ready = py_ready and all(
        item["status"] == "ok"
        for item in programs.values()
        if item["tier"] == 1 and item["required"]
    )
    ready = core_ready and all(
        item["status"] == "ok"
        for item in programs.values()
        if item["required"]
    )
    return {
        "schema_version": "byteworker-runtime-check/v1",
        "ready": ready,
        "core_ready": core_ready,
        "required_sources": sorted(required),
        "python": python,
        "programs": programs,
    }


def render_dependency_report(result: Mapping[str, Any]) -> str:
    lines = ["── Tier 1 · Byteworker 核心 ──"]
    python = result["python"]
    marker = "✓" if python["status"] == "ok" else "✗"
    lines.append(f"  {marker} python {python['version']} · {python['path']}")
    for name, item in result["programs"].items():
        if item["tier"] != 1:
            continue
        marker = "✓" if item["status"] == "ok" else "✗"
        detail = item["version"] or item["error"]
        lines.append(f"  {marker} {name}" + (f" · {detail}" if detail else ""))
    optional = [
        (name, item)
        for name, item in result["programs"].items()
        if item["tier"] == 2
    ]
    if optional:
        lines.append("")
        lines.append("── Tier 2 · 已请求的内部来源 runtime ──")
        for name, item in optional:
            marker = "✓" if item["status"] == "ok" else "✗"
            detail = item["version"] or item["error"]
            lines.append(f"  {marker} {name}" + (f" · {detail}" if detail else ""))
    lines.append("")
    lines.append("结论: " + ("✓ 依赖齐全。" if result["ready"] else "✗ 存在必须修复的依赖问题。"))
    return "\n".join(lines)


def _program_still_valid(path: str, environ: Mapping[str, str]) -> bool:
    if not path:
        return False
    candidate = Path(path).expanduser()
    if not candidate.is_file():
        return False
    if not os.access(candidate, os.X_OK):
        return False
    return True


def _python_still_valid(
    path: str,
    environ: Mapping[str, str],
    *,
    runner=subprocess.run,
) -> bool:
    if not _program_still_valid(path, environ):
        return False
    try:
        completed = runner(
            [
                path,
                "-c",
                "import sys,zoneinfo;raise SystemExit(0 if sys.version_info>=(3,10) else 1)",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
            env=dict(environ),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def read_runtime_cache(
    root: Path,
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> tuple[dict[str, Any] | None, str]:
    env = dict(os.environ if environ is None else environ)
    cache_path = root / RUNTIME_CACHE_FILENAME
    if not cache_path.is_file():
        return None, "cache file missing"
    try:
        raw = cache_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        return None, "cache corrupt: " + _bounded(str(exc), 120)
    if data.get("schema_version") != CACHE_SCHEMA_VERSION:
        return None, "cache schema outdated"
    explicit_overrides = {
        name: env.get(var, "")
        for name, var in EXPLICIT_ENV.items()
        if env.get(var, "")
    }
    if env.get("BYTEWORKER_PYTHON_BIN", "") and env["BYTEWORKER_PYTHON_BIN"] != data.get(
        "python", {}
    ).get("path", ""):
        return None, "BYTEWORKER_PYTHON_BIN overrides cached value"
    for program_name, override_value in explicit_overrides.items():
        cached_path = data.get("programs", {}).get(program_name, {}).get("path", "")
        if override_value != cached_path:
            return None, f"{EXPLICIT_ENV[program_name]} overrides cached value"
    return data, "ok"


def write_runtime_cache(
    root: Path,
    result: Mapping[str, Any],
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> tuple[bool, str]:
    payload = {
        "schema_version": CACHE_SCHEMA_VERSION,
        "generated_at": time.time(),
        "python": result.get("python", {}),
        "programs": result.get("programs", {}),
        "ready": result.get("ready", False),
        "core_ready": result.get("core_ready", False),
    }
    runtime_cache_path = root / RUNTIME_CACHE_FILENAME
    try:
        runtime_cache_path.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
    except OSError as exc:
        return False, "cache write failed: " + _bounded(str(exc), 120)
    return True, "ok"


def clear_runtime_cache(root: Path) -> tuple[bool, str]:
    removed = False
    messages: list[str] = []
    for filename in (PYTHON_CACHE_FILENAME, RUNTIME_CACHE_FILENAME):
        path = root / filename
        if path.is_file():
            try:
                path.unlink()
                removed = True
                messages.append(f"removed {filename}")
            except OSError as exc:
                messages.append(f"{filename}: {_bounded(str(exc), 80)}")
    return removed, ", ".join(messages) if messages else "no cache files present"


def cached_check_runtime(
    root: Path,
    *,
    required_sources: Iterable[str] = (),
    include_optional: bool = False,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
    python_executable: str | None = None,
    python_version: tuple[int, int, int] | None = None,
    runner=subprocess.run,
    force_refresh: bool = False,
) -> tuple[dict[str, Any], str]:
    env = dict(os.environ if environ is None else environ)
    force_refresh = force_refresh or bool(
        env.get("BYTEWORKER_REFRESH_RUNTIME", "").strip()
    )
    home_path = home or Path(env.get("HOME", str(Path.home())))
    cache_status = "bypassed"
    result: dict[str, Any] | None = None
    if not force_refresh:
        cached, _reason = read_runtime_cache(root, environ=env, home=home_path)
        if cached is not None:
            required = set(required_sources)
            cached_programs = {
                name: dict(item)
                for name, item in cached.get("programs", {}).items()
            }
            all_present = True
            for program_name, spec in PROGRAM_SPECS.items():
                source = spec.get("source")
                is_required = (
                    spec["tier"] == 1
                    or source in required
                    or (
                        bool(EXPLICIT_ENV.get(program_name))
                        and env.get(EXPLICIT_ENV[program_name], "")
                    )
                )
                if program_name in cached_programs:
                    cached_programs[program_name]["required"] = is_required
                if spec["tier"] == 2 and not (include_optional or is_required):
                    continue
                if program_name not in cached_programs:
                    all_present = False
                    break
                program_entry = cached_programs[program_name]
                if is_required and program_entry["status"] != "ok":
                    all_present = False
                    break
                if program_entry["status"] == "ok" and not _program_still_valid(
                    program_entry.get("path", ""), env
                ):
                    all_present = False
                    break
            if all_present:
                py_path = cached.get("python", {}).get("path", "")
                if _python_still_valid(py_path, env, runner=runner):
                    python_entry = dict(cached.get("python", {}))
                    core_ready = python_entry.get("status") == "ok" and all(
                        item["status"] == "ok"
                        for item in cached_programs.values()
                        if item["tier"] == 1 and item["required"]
                    )
                    ready = core_ready and all(
                        item["status"] == "ok"
                        for item in cached_programs.values()
                        if item["required"]
                    )
                    result = {
                        "schema_version": "byteworker-runtime-check/v1",
                        "ready": ready,
                        "core_ready": core_ready,
                        "required_sources": sorted(required),
                        "python": python_entry,
                        "programs": cached_programs,
                    }
                    cache_status = "hit"
    if result is None:
        result = check_runtime(
            required_sources=required_sources,
            include_optional=include_optional,
            environ=env,
            home=home_path,
            python_executable=python_executable,
            python_version=python_version,
            runner=runner,
        )
        cache_status = "miss"
        written, _write_status = write_runtime_cache(
            root, result, environ=env, home=home_path
        )
        if written:
            cache_status = "miss+cached"
    return result, cache_status
