"""Executable source-boundary contracts for byteworker.

Adapters may use completely different transports and provider payloads.  They
must, however, hand the digest pipeline the same validated ``SourceBundle``.
"""

from .models import (
    BUNDLE_SCHEMA,
    Coverage,
    RecordIndexEntry,
    SourceBundle,
    SourceBundleError,
    SourceCapabilities,
    SourceComponent,
    SourceIdentity,
    SourceRef,
    canonical_sha256,
    load_source_bundle,
    validate_source_bundle,
)
from .registry import (
    SourceAdapterRegistry,
    SourceRegistryError,
    create_default_registry,
)
from .transaction_bridge import (
    SUPPORTED_TRANSACTION_SOURCE_TYPES,
    SourceTransactionError,
    raw_source_fields,
    validate_transaction_source,
)
from .record_projection import (
    STRUCTURED_SOURCE_TYPES,
    project_legacy_record,
)
from .adapters.base import Capabilities, SourceAdapter
from .adapters.feishu_doc import (
    FeishuDocumentAdapter,
    build_feishu_document_bundle,
    feishu_document_bundle_to_transaction_source,
    feishu_document_to_bundle,
)
from .adapters.meego import (
    MeegoCaptureAdapter,
    build_meego_bundle,
    meego_bundle_to_transaction_source,
    meego_capture_to_bundle,
)

__all__ = [
    "BUNDLE_SCHEMA",
    "Capabilities",
    "Coverage",
    "FeishuDocumentAdapter",
    "MeegoCaptureAdapter",
    "RecordIndexEntry",
    "SourceAdapter",
    "SourceAdapterRegistry",
    "SourceBundle",
    "SourceBundleError",
    "SourceCapabilities",
    "SourceComponent",
    "SourceIdentity",
    "SourceRef",
    "SourceRegistryError",
    "SourceTransactionError",
    "STRUCTURED_SOURCE_TYPES",
    "SUPPORTED_TRANSACTION_SOURCE_TYPES",
    "build_feishu_document_bundle",
    "build_meego_bundle",
    "canonical_sha256",
    "create_default_registry",
    "feishu_document_bundle_to_transaction_source",
    "feishu_document_to_bundle",
    "load_source_bundle",
    "meego_bundle_to_transaction_source",
    "meego_capture_to_bundle",
    "project_legacy_record",
    "raw_source_fields",
    "validate_source_bundle",
    "validate_transaction_source",
]
