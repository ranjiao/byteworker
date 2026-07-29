"""Provider adapters that emit validated source bundles."""

from .base import Capabilities, SourceAdapter
from .feishu_doc import (
    FeishuDocumentAdapter,
    build_feishu_document_bundle,
    feishu_document_bundle_to_transaction_source,
    feishu_document_to_bundle,
)
from .meego import (
    MeegoCaptureAdapter,
    build_meego_bundle,
    meego_bundle_to_transaction_source,
    meego_capture_to_bundle,
)

__all__ = [
    "Capabilities",
    "FeishuDocumentAdapter",
    "MeegoCaptureAdapter",
    "SourceAdapter",
    "build_feishu_document_bundle",
    "build_meego_bundle",
    "feishu_document_bundle_to_transaction_source",
    "feishu_document_to_bundle",
    "meego_bundle_to_transaction_source",
    "meego_capture_to_bundle",
]
