"""Provider-neutral Source Profile errors shared across validation layers."""

from __future__ import annotations


class SourceProfileError(RuntimeError):
    """Safe validation or local-transaction error."""

    def __init__(self, code: str, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint
