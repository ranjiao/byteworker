"""Registry for source adapters.

The registry removes source-type conditionals from callers without pretending
that provider implementations are homogeneous.
"""

from __future__ import annotations

from typing import Any

from .adapters.base import Capabilities, SourceAdapter
from .models import SourceBundle


class SourceRegistryError(LookupError):
    pass


class SourceAdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, SourceAdapter] = {}

    def register(
        self,
        adapter: SourceAdapter,
        *,
        replace: bool = False,
    ) -> None:
        source_type = str(getattr(adapter, "source_type", "")).strip()
        capabilities = getattr(adapter, "capabilities", None)
        build_bundle = getattr(adapter, "build_bundle", None)
        to_transaction_source = getattr(adapter, "to_transaction_source", None)
        if not source_type or not isinstance(capabilities, Capabilities):
            raise SourceRegistryError("adapter 必须声明 source_type 与 Capabilities")
        if not callable(build_bundle):
            raise SourceRegistryError("adapter 必须实现 build_bundle")
        if not callable(to_transaction_source):
            raise SourceRegistryError("adapter 必须实现 to_transaction_source")
        if source_type in self._adapters and not replace:
            raise SourceRegistryError(f"source adapter 已注册: {source_type}")
        self._adapters[source_type] = adapter

    def get(self, source_type: str) -> SourceAdapter:
        normalized = str(source_type).strip()
        try:
            return self._adapters[normalized]
        except KeyError as exc:
            raise SourceRegistryError(
                f"未注册 source adapter: {normalized or 'missing'}"
            ) from exc

    def capabilities(self, source_type: str) -> Capabilities:
        return self.get(source_type).capabilities

    def build_bundle(self, source_type: str, **kwargs: Any) -> SourceBundle:
        return self.get(source_type).build_bundle(**kwargs)

    def to_transaction_source(self, bundle: SourceBundle) -> dict[str, Any]:
        return self.get(bundle.identity.source_type).to_transaction_source(bundle)

    def source_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._adapters))


def create_default_registry() -> SourceAdapterRegistry:
    from .adapters.feishu_doc import FeishuDocumentAdapter
    from .adapters.meego import MeegoCaptureAdapter

    registry = SourceAdapterRegistry()
    registry.register(FeishuDocumentAdapter())
    registry.register(MeegoCaptureAdapter())
    return registry
