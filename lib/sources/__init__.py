"""Executable source-boundary contracts for byteworker.

Adapters may use completely different transports and provider payloads.  They
must, however, hand the digest pipeline the same validated ``SourceBundle``.
"""

from .models import (
    BUNDLE_SCHEMA,
    Coverage,
    RECORD_INDEX_SCHEMA,
    RecordIndexEntry,
    SourceBundle,
    SourceBundleError,
    SourceCapabilities,
    SourceComponent,
    SourceIdentity,
    SourceRef,
    canonical_sha256,
    ensure_source_request_safe,
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
from .adapters.aeolus import (
    AeolusCaptureAdapter,
    aeolus_bundle_to_transaction_source,
    build_aeolus_bundle,
)
from .adapters.feishu_base import (
    FeishuBaseCaptureAdapter,
    build_feishu_base_bundle,
    feishu_base_bundle_to_transaction_source,
)
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
from .adapters.text import (
    FeishuChatAdapter,
    FeishuMinutesAdapter,
    LocalMarkdownAdapter,
    WebAdapter,
    build_feishu_chat_bundle,
    build_feishu_minutes_bundle,
    build_local_markdown_bundle,
    build_web_bundle,
    feishu_chat_bundle_to_transaction_source,
    feishu_minutes_bundle_to_transaction_source,
    local_markdown_bundle_to_transaction_source,
    web_bundle_to_transaction_source,
)

__all__ = [
    "AeolusCaptureAdapter",
    "BUNDLE_SCHEMA",
    "Capabilities",
    "Coverage",
    "FeishuBaseCaptureAdapter",
    "FeishuChatAdapter",
    "FeishuDocumentAdapter",
    "FeishuMinutesAdapter",
    "LocalMarkdownAdapter",
    "MeegoCaptureAdapter",
    "RECORD_INDEX_SCHEMA",
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
    "WebAdapter",
    "aeolus_bundle_to_transaction_source",
    "build_aeolus_bundle",
    "build_feishu_base_bundle",
    "build_feishu_chat_bundle",
    "build_feishu_document_bundle",
    "build_feishu_minutes_bundle",
    "build_local_markdown_bundle",
    "build_meego_bundle",
    "build_web_bundle",
    "canonical_sha256",
    "ensure_source_request_safe",
    "create_default_registry",
    "feishu_base_bundle_to_transaction_source",
    "feishu_chat_bundle_to_transaction_source",
    "feishu_document_bundle_to_transaction_source",
    "feishu_document_to_bundle",
    "feishu_minutes_bundle_to_transaction_source",
    "local_markdown_bundle_to_transaction_source",
    "load_source_bundle",
    "meego_bundle_to_transaction_source",
    "meego_capture_to_bundle",
    "project_legacy_record",
    "raw_source_fields",
    "validate_source_bundle",
    "validate_transaction_source",
    "web_bundle_to_transaction_source",
]
