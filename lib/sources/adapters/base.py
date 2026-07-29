"""Adapter protocol and capability declaration."""

from __future__ import annotations

from typing import Any, ClassVar, Protocol, runtime_checkable

from ..models import SourceBundle, SourceCapabilities


Capabilities = SourceCapabilities


@runtime_checkable
class SourceAdapter(Protocol):
    source_type: ClassVar[str]
    capabilities: ClassVar[Capabilities]

    def build_bundle(self, **kwargs: Any) -> SourceBundle:
        """Build and validate a bundle without network or filesystem writes."""

    def to_transaction_source(self, bundle: SourceBundle) -> dict[str, Any]:
        """Materialize the current digest-plan/v1 compatibility source."""
