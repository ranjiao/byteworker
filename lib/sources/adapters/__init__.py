"""Provider adapters that emit validated source bundles."""

from .base import Capabilities, SourceAdapter
from .aeolus import (
    AeolusCaptureAdapter,
    aeolus_bundle_to_transaction_source,
    build_aeolus_bundle,
)
from .feishu_base import (
    FeishuBaseCaptureAdapter,
    build_feishu_base_bundle,
    feishu_base_bundle_to_transaction_source,
)
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
from .text import (
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
    "Capabilities",
    "FeishuBaseCaptureAdapter",
    "FeishuChatAdapter",
    "FeishuDocumentAdapter",
    "FeishuMinutesAdapter",
    "LocalMarkdownAdapter",
    "MeegoCaptureAdapter",
    "SourceAdapter",
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
    "feishu_base_bundle_to_transaction_source",
    "feishu_chat_bundle_to_transaction_source",
    "feishu_document_bundle_to_transaction_source",
    "feishu_document_to_bundle",
    "feishu_minutes_bundle_to_transaction_source",
    "local_markdown_bundle_to_transaction_source",
    "meego_bundle_to_transaction_source",
    "meego_capture_to_bundle",
    "web_bundle_to_transaction_source",
]
