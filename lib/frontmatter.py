"""
byteworker · frontmatter 解析
统一解析 markdown 文件的 YAML frontmatter + body。
rebuild-index 与 repair-links 共用，保证解析行为一致。
"""
import json
import os
from typing import Dict, Any, Tuple, List, Optional


def parse_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    """
    解析 markdown 文本的 frontmatter。
    返回 (frontmatter_dict, body_text)。
    无 frontmatter 时返回 ({}, text)。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text

    fm_end = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            fm_end = idx
            break
    if fm_end is None:
        return {}, text

    fm: Dict[str, Any] = {}
    current_key: Optional[str] = None

    for line in lines[1:fm_end]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if (line.startswith(" ") or line.startswith("\t")) and stripped.startswith("-") and current_key:
            fm.setdefault(current_key, [])
            if not isinstance(fm[current_key], list):
                fm[current_key] = [fm[current_key]] if fm[current_key] else []
            item = stripped[1:].strip()
            if item:
                if item.startswith('"') and item.endswith('"'):
                    try:
                        item = json.loads(item)
                    except json.JSONDecodeError:
                        item = item.strip('"')
                elif item.startswith("'") and item.endswith("'"):
                    item = item[1:-1]
                fm[current_key].append(item)
            continue

        if ":" not in line:
            current_key = None
            continue

        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        current_key = key

        if val == "":
            fm[key] = []
        elif val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            fm[key] = [x.strip() for x in inner.split(",") if x.strip()] if inner else []
        else:
            fm[key] = val.strip('"').strip("'")

    body = "\n".join(lines[fm_end + 1:])
    return fm, body


def parse_file(path: str) -> Tuple[Dict[str, Any], str]:
    """从文件路径解析 frontmatter。"""
    with open(path, "r", encoding="utf-8") as f:
        return parse_frontmatter(f.read())


def extract_tldr(body: str) -> str:
    """从 body 首行提取 TL;DR。"""
    for line in body.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith(">"):
            s = s[1:].strip()
        lowered = s.lower()
        for marker in ("**tl;dr:**", "tl;dr:", "**tldr:**", "tldr:"):
            if lowered.startswith(marker):
                return s[len(marker):].strip()
        if s.startswith("**TL;DR:**"):
            return s[len("**TL;DR:**"):].strip()
    return ""


def extract_title(body: str, fallback: str = "") -> str:
    """从 body 的 # 标题提取标题。"""
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def source_window_end(value: str) -> str:
    """从 source_window 字段提取结束时间。"""
    if not value:
        return ""
    if ".." in value:
        return value.split("..", 1)[1].strip()
    return value.strip()
