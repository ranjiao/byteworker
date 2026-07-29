"""
byteworker · 知识库常量
"""
from typing import List, Tuple, Dict


NODE_TYPES: List[Tuple[str, str, str]] = [
    ("people",    "person",   "人员"),
    ("projects",  "project",  "项目"),
    ("areas",     "area",     "主题领域"),
    ("orgs",      "org",      "组织"),
    ("events",    "event",    "事件"),
    ("decisions", "decision", "决策"),
    ("readings",  "reading",  "读物"),
]

SOURCE_TYPE_LABELS: Dict[str, str] = {
    "feishu_doc":     "飞书文档",
    "feishu_minutes": "妙记",
    "feishu_meeting": "会议",
    "feishu_chat":    "群聊",
    "feishu_base":    "多维表格",
    "meego":          "Meego 视图",
    "web":           "网页/读物",
    "local_md":      "本地 Markdown",
}

NODE_ID_PREFIXES = (
    "person-", "project-", "area-", "org-",
    "event-", "decision-", "reading-",
)
