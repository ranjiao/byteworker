"""Read the public arguments from the parsers used to execute commands."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=None)
def parser_paths(entrypoint: str) -> dict[tuple[str, ...], tuple[argparse.Action, ...]]:
    path = Path(__file__).resolve().parents[1] / "bin" / entrypoint
    name = "_byteworker_command_" + path.stem.replace("-", "_")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load command parser: {entrypoint}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    paths: dict[tuple[str, ...], tuple[argparse.Action, ...]] = {}

    def visit(parser: argparse.ArgumentParser, prefix: tuple[str, ...], inherited: tuple[argparse.Action, ...] = ()) -> None:
        options = inherited + tuple(
            action for action in parser._actions
            if not isinstance(action, (argparse._SubParsersAction, argparse._HelpAction))
            and action.help != argparse.SUPPRESS
        )
        paths[prefix] = options
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for key, child in action.choices.items():
                    visit(child, (*prefix, key), options)
            elif action.dest in {"command", "operation"} and not action.option_strings and action.choices:
                for key in action.choices:
                    paths[(*prefix, key)] = tuple(item for item in options if item is not action)

    visit(module.parser(), ())
    return paths


def argument_description(action: argparse.Action) -> dict[str, Any]:
    return {
        "name": action.dest,
        "flags": list(action.option_strings),
        "required": action.required,
        "nargs": action.nargs,
        "help": action.help or "",
        **({"choices": list(action.choices)} if action.choices is not None else {}),
        **({"default": action.default} if isinstance(action.default, (bool, int, float)) else {}),
    }
