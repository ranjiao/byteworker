#!/usr/bin/env bash
# byteworker · rebuild-index.sh
# 从 knowledge/ 节点与 raw_data/ frontmatter 确定性重建 INDEX.md。
#
# 用法:
#   bin/rebuild-index.sh [--dry-run] [--kb <数据目录>]
#   bin/rebuild-index.sh --help
#
#   --dry-run   只把生成结果输出到 stdout,不写回 INDEX.md。
#   --kb <dir>  指定知识库数据目录,覆盖 .kbconfig(主要用于测试)。
#
# 做什么:
#   · 扫 7 类 knowledge 节点,生成节点登记表;人员表带 feishu_id 列。
#   · 扫带 routine 的 raw_data,生成「定期摄取清单」。
#   · 扫 feishu_chat raw_data,生成「群聊摄取进度」高水位。
#   · 原子写回 INDEX.md;不碰 git、不写 journal —— 由调用方按「写入规范」收尾。
#
# 退出码:0 成功 | 1 环境或参数错误
set -uo pipefail

SELF_DIR=$(cd "$(dirname "$0")" && pwd)
KBCONFIG="$SELF_DIR/../.kbconfig"

KB=""; DRYRUN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --kb)      KB="${2:-}"; shift 2;;
    --dry-run) DRYRUN=1; shift;;
    -h|--help) sed -n '2,24p' "$0"; exit 0;;
    *) echo "未知参数:$1(用 --help 看用法)" >&2; exit 1;;
  esac
done

if [ -z "$KB" ]; then
  [ -f "$KBCONFIG" ] || { echo "错误:未找到 .kbconfig(byteworker 尚未首次配置);或用 --kb 指定数据目录" >&2; exit 1; }
  KB=$(head -n1 "$KBCONFIG" | tr -d '[:space:]')
fi
[ -n "$KB" ] && [ -d "$KB" ] || { echo "错误:知识库数据目录不存在:$KB" >&2; exit 1; }
[ -d "$KB/knowledge" ] || { echo "错误:$KB 下没有 knowledge/,似乎不是知识库数据目录" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "错误:未找到 python3" >&2; exit 1; }

python3 - "$KB" "$DRYRUN" <<'PY'
import glob
import os
import sys

KB = sys.argv[1]
DRYRUN = sys.argv[2] == "1"

SECTIONS = [
    ("people", "person", "人员"),
    ("projects", "project", "项目"),
    ("areas", "area", "主题领域"),
    ("orgs", "org", "组织"),
    ("events", "event", "事件"),
    ("decisions", "decision", "决策"),
    ("readings", "reading", "读物"),
]

TYPE_LABELS = {
    "feishu_doc": "飞书文档",
    "feishu_minutes": "妙记",
    "feishu_meeting": "会议",
    "feishu_chat": "群聊",
    "web": "网页/读物",
    "local_md": "本地 Markdown",
}


def parse_frontmatter(path):
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
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

    fm = {}
    current_key = None
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
    body = "\n".join(lines[fm_end + 1 :])
    return fm, body


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


def first_tldr(body):
    for line in body.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith(">"):
            s = s[1:].strip()
        lowered = s.lower()
        for marker in ("**tl;dr:**", "tl;dr:", "**tldr:**", "tldr:"):
            if lowered.startswith(marker):
                return s[len(marker) :].strip()
        if s.startswith("**TL;DR:**"):
            return s[len("**TL;DR:**") :].strip()
    return ""


def title_from_body(body, fallback):
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def source_window_end(value):
    if not value:
        return ""
    if ".." in value:
        return value.split("..", 1)[1].strip()
    return value.strip()


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


def render_node_section(dir_name, node_type, label):
    paths = sorted(glob.glob(os.path.join(KB, "knowledge", dir_name, "**", "*.md"), recursive=True))
    rows = []
    malformed = []
    for path in paths:
        fm, body = parse_frontmatter(path)
        node_id = fm.get("id", "")
        if not node_id:
            malformed.append(os.path.relpath(path, KB))
            continue
        title = fm.get("title") or title_from_body(body, node_id)
        tldr = first_tldr(body)
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


def render_raw_sections():
    raw_paths = sorted(glob.glob(os.path.join(KB, "raw_data", "*.md")))
    routine = {}
    chats = {}
    pending = failed = 0

    for path in raw_paths:
        fm, _ = parse_frontmatter(path)
        if not fm:
            continue
        status = fm.get("digest_status", "")
        pending += 1 if status == "pending" else 0
        failed += 1 if status == "failed" else 0

        raw_id = fm.get("raw_id") or os.path.splitext(os.path.basename(path))[0]
        source_type = fm.get("source_type", "")
        last_seen = raw_last_seen(fm)
        targets = list_value(fm.get("digest_targets"))

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
            key = raw_source_key(fm)
            if not key:
                continue
            old = routine.get(key)
            if old is None or last_seen > old["last_seen"]:
                routine[key] = {
                    "source": raw_source_label(fm),
                    "type": TYPE_LABELS.get(source_type, source_type or "-"),
                    "cadence": cadence,
                    "last_seen": last_seen,
                    "targets": targets,
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


doc = [
    "# 知识库索引",
    "",
    "<!-- 此文件由 byteworker 维护;可用 bin/rebuild-index.sh 从 knowledge/ 与 raw_data/ 重建。 -->",
    "",
]
counts = {}
malformed = []
for dir_name, node_type, label in SECTIONS:
    section, count, bad = render_node_section(dir_name, node_type, label)
    doc.extend(section)
    counts[node_type] = count
    malformed.extend(bad)

raw_section, routine_count, chat_count, pending_count, failed_count = render_raw_sections()
doc.extend(raw_section)

output = "\n".join(doc).rstrip() + "\n"
if DRYRUN:
    sys.stdout.write(output)
else:
    tmp = os.path.join(KB, "INDEX.md.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(output)
    os.replace(tmp, os.path.join(KB, "INDEX.md"))

print("byteworker · INDEX 重建" + ("(dry-run,不写回)" if DRYRUN else ""), file=sys.stderr)
print("数据目录:%s" % KB, file=sys.stderr)
for _, node_type, _ in SECTIONS:
    print("%s=%d" % (node_type, counts[node_type]), file=sys.stderr)
print("routine_sources=%d" % routine_count, file=sys.stderr)
print("chat_sources=%d" % chat_count, file=sys.stderr)
print("raw_pending=%d" % pending_count, file=sys.stderr)
print("raw_failed=%d" % failed_count, file=sys.stderr)
if malformed:
    print("malformed_nodes=%d" % len(malformed), file=sys.stderr)
    for path in malformed:
        print("  %s" % path, file=sys.stderr)
print("mode=%s" % ("dry-run" if DRYRUN else "apply"), file=sys.stderr)
PY
exit $?
