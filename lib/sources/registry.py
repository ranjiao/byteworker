"""Registry for source adapters.

The registry removes source-type conditionals from callers without pretending
that provider implementations are homogeneous.
"""

from __future__ import annotations

import inspect
from typing import Any

from .adapters.base import Capabilities, SourceAdapter
from .models import SourceBundle, SourceBundleError
from .request_specs import request_spec


class SourceRegistryError(LookupError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "SOURCE_ADAPTER_UNAVAILABLE",
    ) -> None:
        super().__init__(message)
        self.code = code


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
        request_builder = getattr(adapter, "request_builder", None)
        build_bundle = getattr(adapter, "build_bundle", None)
        validate_bundle = getattr(adapter, "validate_bundle", None)
        to_transaction_source = getattr(adapter, "to_transaction_source", None)
        if not source_type or not isinstance(capabilities, Capabilities):
            raise SourceRegistryError("adapter 必须声明 source_type 与 Capabilities")
        if not callable(request_builder):
            raise SourceRegistryError("adapter 必须声明 request_builder")
        if not callable(build_bundle):
            raise SourceRegistryError("adapter 必须实现 build_bundle")
        if not callable(validate_bundle):
            raise SourceRegistryError("adapter 必须实现 validate_bundle")
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

    def request_spec(self, source_type: str) -> dict[str, Any]:
        adapter = self.get(source_type)
        return request_spec(source_type, adapter.request_builder)

    def build_bundle(self, source_type: str, **kwargs: Any) -> SourceBundle:
        adapter = self.get(source_type)
        try:
            inspect.signature(adapter.request_builder).bind(**kwargs)
        except TypeError as exc:
            raise SourceRegistryError(
                f"{source_type} bundle request 参数不符合 adapter 契约: {exc}",
                code="SOURCE_BUNDLE_REQUEST_INVALID",
            ) from exc
        try:
            return adapter.build_bundle(**kwargs)
        except TypeError as exc:
            raise SourceRegistryError(
                f"{source_type} adapter 内部类型错误: {exc}",
                code="SOURCE_ADAPTER_INTERNAL_ERROR",
            ) from exc

    def to_transaction_source(self, bundle: SourceBundle) -> dict[str, Any]:
        adapter = self.get(bundle.identity.source_type)
        unsupported = sorted(
            {
                component.kind
                for component in bundle.components
                if component.kind not in adapter.capabilities.component_kinds
            }
        )
        if unsupported:
            raise SourceBundleError(
                "SOURCE_BUNDLE_ADAPTER_INVALID",
                f"{bundle.identity.source_type} bundle 含 adapter 未声明的 "
                f"component kind: {', '.join(unsupported)}",
                path="components",
            )
        adapter.validate_bundle(bundle)
        return adapter.to_transaction_source(bundle)

    def source_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._adapters))


def create_default_registry() -> SourceAdapterRegistry:
    from .adapters.aeolus import AeolusCaptureAdapter
    from .adapters.feishu_doc import FeishuDocumentAdapter
    from .adapters.feishu_base import FeishuBaseCaptureAdapter
    from .adapters.meego import MeegoCaptureAdapter
    from .adapters.text import (
        FeishuChatAdapter,
        FeishuMinutesAdapter,
        LocalMarkdownAdapter,
        WebAdapter,
    )

    registry = SourceAdapterRegistry()
    registry.register(AeolusCaptureAdapter())
    registry.register(FeishuBaseCaptureAdapter())
    registry.register(FeishuChatAdapter())
    registry.register(FeishuDocumentAdapter())
    registry.register(FeishuMinutesAdapter())
    registry.register(LocalMarkdownAdapter())
    registry.register(MeegoCaptureAdapter())
    registry.register(WebAdapter())
    return registry
