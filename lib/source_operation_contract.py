"""Declarative argparse contract for provider operation adapters."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("必须是正整数")
    return parsed


@dataclass(frozen=True)
class OperationArgument:
    flags: tuple[str, ...]
    options: tuple[tuple[str, Any], ...] = ()

    def add_to(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(*self.flags, **dict(self.options))

    @property
    def destination(self) -> str:
        options = dict(self.options)
        if "dest" in options:
            return str(options["dest"])
        return self.flags[-1].lstrip("-").replace("-", "_")

    def to_dict(self) -> dict[str, Any]:
        options = dict(self.options)
        value_type = options.pop("type", None)
        if value_type is not None:
            options["type"] = getattr(value_type, "__name__", str(value_type))
        if isinstance(options.get("choices"), tuple):
            options["choices"] = list(options["choices"])
        return {"flags": list(self.flags), "destination": self.destination, **options}


def argument(*flags: str, **options: Any) -> OperationArgument:
    return OperationArgument(tuple(flags), tuple(options.items()))
