#!/usr/bin/env python3
"""
byteworker · rebuild-index
从 knowledge/ 节点、sources/ profiles 与 raw_data/ frontmatter 确定性重建 INDEX.md。

用法:
  python3 rebuild_index.py <kb_dir> [--dry-run]

退出码: 0 成功 | 1 环境或参数错误
"""
import glob
import os
import sys
from pathlib import Path


def _add_lib_path():
    self_dir = os.path.dirname(os.path.abspath(__file__))
    lib_dir = os.path.join(self_dir, "..", "lib")
    lib_dir = os.path.normpath(lib_dir)
    if lib_dir not in sys.path:
        sys.path.insert(0, lib_dir)


_add_lib_path()

from frontmatter import parse_file, extract_tldr, extract_title, source_window_end
from constants import NODE_TYPES, SOURCE_TYPE_LABELS
from source_profiles import list_profiles


def esc(value):
    value = "" if value is None else str(value)
    value = value.replace("\n", " ").strip()
    return value.replace("|", "\\|") or "-"


def list_value(value):
    if isinstance(value, list):
        return value
    if not value:
        return []
    return [str(value)]


def raw_last_seen(fm):
    source_type = fm.get("source_type", "")
    if source_type == "feishu_chat":
        return source_window_end(fm.get("source_window", ""))
    period = fm.get("digest_period", "")
    if period:
        return period
    ingested = fm.get("ingested", "")
    if ingested:
        return ingested[:10]
    return ""


def raw_source_key(fm):
    source_type = fm.get("source_type", "")
    source_uid = fm.get("source_uid", "")
    if source_uid:
        return source_uid
    if source_type == "feishu_chat":
        return fm.get("source_chat_id") or fm.get("source_chat_name") or fm.get("raw_id")
    return fm.get("source_url") or fm.get("source_title") or fm.get("raw_id")


def raw_source_label(fm):
    source_type = fm.get("source_type", "")
    if source_type == "feishu_chat":
        return fm.get("source_chat_name") or fm.get("source_chat_id") or fm.get("raw_id")
    title = fm.get("source_title") or ""
    url = fm.get("source_url") or ""
    return title or url or fm.get("raw_id", "")


def render_node_section(kb, dir_name, node_type, label):
    paths = sorted(glob.glob(os.path.join(kb, "knowledge", dir_name, "**", "*.md"), recursive=True))
    rows = []
    malformed = []
    for path in paths:
        fm, body = parse_file(path)
        node_id = fm.get("id", "")
        if not node_id:
            malformed.append(os.path.relpath(path, kb))
            continue
        title = fm.get("title") or extract_title(body, node_id)
        tldr = extract_tldr(body)
        status = fm.get("status", "")
        last_verified = fm.get("last_verified", fm.get("updated", ""))
        if node_type == "person":
            rows.append((node_id, title, fm.get("feishu_id", "?"), tldr, status, last_verified))
        else:
            rows.append((node_id, title, tldr, status, last_verified))
    rows.sort(key=lambda x: x[0])

    out = [f"## {label} ({node_type})"]
    if node_type == "person":
        out += [
            "| id | 标题 | feishu_id | TL;DR | status | last_verified |",
            "|----|------|-----------|-------|--------|----------------|",
        ]
        for row in rows:
            out.append("| " + " | ".join(esc(x) for x in row) + " |")
    else:
        out += [
            "| id | 标题 | TL;DR | status | last_verified |",
            "|----|------|-------|--------|----------------|",
        ]
        for row in rows:
            out.append("| " + " | ".join(esc(x) for x in row) + " |")
    out.append("")
    return out, len(rows), malformed


def render_raw_sections(kb):
    raw_paths = sorted(glob.glob(os.path.join(kb, "raw_data", "*.md")))
    routine = {}
    latest_by_source = {}
    chats = {}
    pending = failed = 0

    for path in raw_paths:
        fm, _ = parse_file(path)
        if not fm:
            continue
        status = fm.get("digest_status", "")
        pending += 1 if status == "pending" else 0
        failed += 1 if status == "failed" else 0

        raw_id = fm.get("raw_id") or os.path.splitext(os.path.basename(path))[0]
        source_type = fm.get("source_type", "")
        last_seen = raw_last_seen(fm)
        targets = list_value(fm.get("digest_targets"))
        source_key = raw_source_key(fm)
        if source_key:
            old = latest_by_source.get(source_key)
            if old is None or last_seen > old["last_seen"]:
                latest_by_source[source_key] = {
                    "source": raw_source_label(fm),
                    "type": SOURCE_TYPE_LABELS.get(
                        source_type,
                        source_type or "-",
                    ),
                    "last_seen": last_seen,
                    "targets": targets,
                }

        if source_type == "feishu_chat":
            chat_id = fm.get("source_chat_id", "")
            if chat_id:
                end = source_window_end(fm.get("source_window", ""))
                old = chats.get(chat_id)
                if old is None or end > old["end"]:
                    chats[chat_id] = {
                        "name": fm.get("source_chat_name", ""),
                        "chat_id": chat_id,
                        "end": end,
                        "raw_id": raw_id,
                    }

        cadence = fm.get("routine", "")
        if cadence:
            key = source_key
            if not key:
                continue
            old = routine.get(key)
            if old is None or last_seen > old["last_seen"]:
                routine[key] = {
                    "source": raw_source_label(fm),
                    "type": SOURCE_TYPE_LABELS.get(source_type, source_type or "-"),
                    "cadence": cadence,
                    "last_seen": last_seen,
                    "targets": targets,
                }

    profiles = list_profiles(Path(kb))
    profile_uids = {profile["source_uid"] for profile in profiles}
    # A profile is the operational truth for a structured source.  Its
    # presence suppresses legacy raw-frontmatter routine settings, including
    # when routine is explicitly disabled.
    routine = {
        key: item
        for key, item in routine.items()
        if key not in profile_uids
    }
    for profile in profiles:
        if not profile["routine"]["enabled"]:
            continue
        latest = latest_by_source.get(profile["source_uid"], {})
        routine[profile["source_uid"]] = {
            "source": profile["title"],
            "type": SOURCE_TYPE_LABELS.get(
                profile["source_type"],
                profile["source_type"],
            ),
            "cadence": profile["routine"]["cadence"],
            "last_seen": latest.get("last_seen", ""),
            "targets": latest.get("targets", []),
        }

    out = [
        "## 定期摄取清单 (routine digest — 会定期更新、需周期性复查的源)",
        "| 源 | 类型 | cadence | 上次摄取 | 关联节点 |",
        "|----|------|---------|----------|----------|",
    ]
    for item in sorted(routine.values(), key=lambda x: (x["type"], x["source"])):
        out.append("| %s | %s | %s | %s | %s |" % (
            esc(item["source"]),
            esc(item["type"]),
            esc(item["cadence"]),
            esc(item["last_seen"]),
            esc(", ".join(item["targets"])),
        ))
    out.append("")

    out += [
        "## 群聊摄取进度 (feishu_chat 增量高水位)",
        "| 群名 | chat_id | 已摄取至 | 最近 raw_id |",
        "|------|---------|----------|-------------|",
    ]
    for item in sorted(chats.values(), key=lambda x: (x["name"], x["chat_id"])):
        out.append("| %s | %s | %s | %s |" % (
            esc(item["name"]),
            esc(item["chat_id"]),
            esc(item["end"]),
            esc(item["raw_id"]),
        ))
    out.append("")
    return out, len(routine), len(chats), pending, failed


def main():
    if len(sys.argv) < 2:
        print("用法: rebuild_index.py <kb_dir> [--dry-run]", file=sys.stderr)
        sys.exit(1)

    kb = sys.argv[1]
    dry_run = "--dry-run" in sys.argv

    if not os.path.isdir(kb):
        print("错误: 知识库数据目录不存在: %s" % kb, file=sys.stderr)
        sys.exit(1)
    if not os.path.isdir(os.path.join(kb, "knowledge")):
        print("错误: %s 下没有 knowledge/, 似乎不是知识库数据目录" % kb, file=sys.stderr)
        sys.exit(1)

    doc = [
        "# 知识库索引",
        "",
        "<!-- 此文件由 byteworker 维护;可用 bin/rebuild-index.sh 从 knowledge/、sources/ 与 raw_data/ 重建。 -->",
        "",
    ]
    counts = {}
    malformed = []
    for dir_name, node_type, label in NODE_TYPES:
        section, count, bad = render_node_section(kb, dir_name, node_type, label)
        doc.extend(section)
        counts[node_type] = count
        malformed.extend(bad)

    raw_section, routine_count, chat_count, pending_count, failed_count = render_raw_sections(kb)
    doc.extend(raw_section)

    output = "\n".join(doc).rstrip() + "\n"
    if dry_run:
        sys.stdout.write(output)
    else:
        tmp = os.path.join(kb, "INDEX.md.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(output)
        os.replace(tmp, os.path.join(kb, "INDEX.md"))

    label = "(dry-run, 不写回)" if dry_run else ""
    print("byteworker · INDEX 重建%s" % label, file=sys.stderr)
    print("数据目录: %s" % kb, file=sys.stderr)
    for _, node_type, _ in NODE_TYPES:
        print("%s=%d" % (node_type, counts[node_type]), file=sys.stderr)
    print("routine_sources=%d" % routine_count, file=sys.stderr)
    print("chat_sources=%d" % chat_count, file=sys.stderr)
    print("raw_pending=%d" % pending_count, file=sys.stderr)
    print("raw_failed=%d" % failed_count, file=sys.stderr)
    if malformed:
        print("malformed_nodes=%d" % len(malformed), file=sys.stderr)
        for path in malformed:
            print("  %s" % path, file=sys.stderr)
    print("mode=%s" % ("dry-run" if dry_run else "apply"), file=sys.stderr)


if __name__ == "__main__":
    main()
