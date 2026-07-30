"""Machine-readable request contracts for SourceBundle adapters."""

from __future__ import annotations

import inspect
from typing import Any, Callable


REQUEST_ARTIFACT_FIELDS = {
    "aeolus": ("capture_path",),
    "feishu_base": ("capture_path",),
    "feishu_chat": ("transcript", "locator_artifact"),
    "feishu_doc": ("body", "comments", "whiteboards"),
    "feishu_minutes": ("transcript",),
    "local_md": ("local_file",),
    "meego": ("capture_path",),
    "web": ("body",),
}

SOURCE_UID_RULES = {
    "aeolus": "aeolus:<region>:<app_id>:<dashboard_id>:<sheet_id>",
    "feishu_base": "feishu_base:<base_token>:<table_id>:<view_id>",
    "feishu_chat": "直接使用 source_chat_id，不添加 feishu_chat: 前缀",
    "feishu_doc": (
        "直接使用 document_id 或 wiki token，不添加 feishu_doc: 前缀"
    ),
    "feishu_minutes": (
        "直接使用 minute token，不添加 feishu_minutes: 前缀"
    ),
    "local_md": "使用稳定的调用方命名或本地文件绝对路径",
    "meego": "meego:<project_key>:<view_id>",
    "web": "使用去除无意义跟踪参数后的规范 URL",
}

REQUEST_EXAMPLES: dict[str, dict[str, Any]] = {
    "aeolus": {"capture_path": "/tmp/byteworker/aeolus-capture.json"},
    "feishu_base": {"capture_path": "/tmp/byteworker/base-capture.json"},
    "feishu_chat": {
        "source_uid": "oc_xxx",
        "title": "项目群",
        "source_window": (
            "2026-07-30T09:00:00+08:00 .. "
            "2026-07-30T18:00:00+08:00"
        ),
        "transcript": {"path": "/tmp/byteworker/chat-transcript.txt"},
    },
    "feishu_doc": {
        "source_uid": "doxcnxxx",
        "source_url": "https://example.feishu.cn/docx/doxcnxxx",
        "title": "示例文档",
        "revision": "12",
        "body": {
            "path": "/tmp/byteworker/doc-fetch.json",
            "mode": "verbatim",
            "json_pointer": "/data/document/content",
        },
    },
    "feishu_minutes": {
        "source_uid": "obcnxxx",
        "source_url": "https://example.feishu.cn/minutes/obcnxxx",
        "title": "示例会议",
        "transcript": {
            "path": "/tmp/byteworker/minutes-transcript.txt",
        },
    },
    "local_md": {
        "source_uid": "direct-user:example",
        "title": "示例资料",
        "local_file": {"path": "/tmp/byteworker/example.md"},
    },
    "meego": {"capture_path": "/tmp/byteworker/meego-capture.json"},
    "web": {
        "source_uid": "https://example.com/article",
        "source_url": "https://example.com/article",
        "title": "示例文章",
        "body": {"path": "/tmp/byteworker/article.md"},
    },
}

REQUEST_COMPONENT_FIELDS = (
    "name",
    "kind",
    "path",
    "mode",
    "json_pointer",
    "heading",
    "uid",
    "media_type",
    "coverage",
)

NORMALIZED_COMPONENT_FIELDS = (
    "name",
    "kind",
    "path",
    "mode",
    "json_pointer",
    "heading",
    "uid",
    "media_type",
)


def request_spec(source_type: str, builder: Callable[..., Any]) -> dict[str, Any]:
    """Describe a strict adapter request without duplicating its top-level signature."""

    required: list[str] = []
    optional: list[str] = []
    for name, parameter in inspect.signature(builder).parameters.items():
        if name in {"skill_root", "capture"}:
            continue
        if parameter.default is inspect.Parameter.empty:
            required.append(name)
        else:
            optional.append(name)
    return {
        "source_type": source_type,
        "transport": {
            "request_argument": "JSON file path",
            "inline_json_supported": False,
            "allowed_locations": ["system_temporary_directory", "knowledge_base"],
        },
        "required_fields": required,
        "optional_fields": optional,
        "artifact_fields": list(REQUEST_ARTIFACT_FIELDS[source_type]),
        "source_uid_rule": SOURCE_UID_RULES[source_type],
        "request_component": {
            "fields": list(REQUEST_COMPONENT_FIELDS),
            "required": ["path"],
            "notes": [
                "coverage 只存在于 adapter request，规范化后进入 bundle.coverage",
                "verbatim + json_pointer 必须定位到 JSON 字符串",
                "canonical-json + json_pointer 会对所选值做规范 JSON 序列化",
            ],
        },
        "normalized_component": {
            "fields": list(NORMALIZED_COMPONENT_FIELDS),
            "required": ["name", "kind", "path", "mode"],
        },
        "example": REQUEST_EXAMPLES[source_type],
    }
