#!/usr/bin/env bash
# byteworker · repair-links.sh
# 校验并修复知识库节点间双向链接(links)的对称性。
# links 是真相源、靠手工维护会漂移;本工具是「重建 INDEX」在链接维度的对应物。
#
# 用法:
#   bin/repair-links.sh [--dry-run] [--autolink] [--kb <数据目录>]
#   bin/repair-links.sh --help
#
#   --dry-run   只检查并报告,不写回任何文件。
#   --autolink  扫描正文 body 中出现的已存在节点 id,并补进 links。
#   --kb <dir>  指定知识库数据目录,覆盖 .kbconfig(主要用于测试)。
#
# 做什么:
#   · 对称性修复 —— 节点 A 链到 B,则确保 B 也链回 A;缺失的反向链接自动补上(原子写)。
#   · 去重     —— 同一 links 列表里重复的 id 合并为一条。
#   · auto-link —— 加 --autolink 时,正文提及的已存在节点 id 自动纳入 links。
#   · 悬空链接 —— A 链到的目标节点不存在:只报告,不修复(交人裁决)。
#   · 自链接   —— A 链到自己:只报告,不修复。
#   只动 frontmatter 的 links 块,其余 frontmatter 与 body 逐字不变。
#   不碰 git、不写 journal —— 由调用方按「写入规范」收尾。
#
# 退出码:0 成功(干净 / 已修复) | 1 环境或参数错误 | 3 完成但存在悬空链接需人工复核
set -uo pipefail

SELF_DIR=$(cd "$(dirname "$0")" && pwd)
KBCONFIG="$SELF_DIR/../.kbconfig"

KB=""; DRYRUN=0; AUTOLINK=0
while [ $# -gt 0 ]; do
  case "$1" in
    --kb)      KB="${2:-}"; shift 2;;
    --dry-run) DRYRUN=1; shift;;
    --autolink) AUTOLINK=1; shift;;
    -h|--help) sed -n '2,21p' "$0"; exit 0;;
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

python3 - "$KB" "$DRYRUN" "$AUTOLINK" <<'PY'
import sys, os, glob, re

KB = sys.argv[1]
DRYRUN = sys.argv[2] == "1"
AUTOLINK = sys.argv[3] == "1"

def parse(path):
    """解析一个节点文件的 frontmatter。返回 dict;无法解析则带 'malformed'。"""
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {"malformed": "无 frontmatter"}
    fm_end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            fm_end = i
            break
    if fm_end is None:
        return {"malformed": "frontmatter 未闭合"}
    node_id = None
    links = []
    links_start = links_end = None
    i = 1
    while i < fm_end:
        ln = lines[i]
        if ln and not ln[0].isspace() and ":" in ln:
            key, _, val = ln.partition(":")
            key = key.strip(); val = val.strip()
            if key == "id":
                node_id = val
            elif key == "links":
                links_start = i
                if val.startswith("["):
                    inner = val[1:val.rfind("]")] if "]" in val else val[1:]
                    links = [x.strip() for x in inner.split(",") if x.strip()]
                    links_end = i + 1
                else:
                    j = i + 1
                    while j < fm_end:
                        s = lines[j]
                        if (s.startswith(" ") or s.startswith("\t")) and s.strip().startswith("-"):
                            item = s.strip()[1:].strip()
                            if item:
                                links.append(item)
                            j += 1
                        else:
                            break
                    links_end = j
                    i = j
                    continue
        i += 1
    if not node_id:
        return {"malformed": "无 id 字段"}
    body = "\n".join(lines[fm_end + 1:])
    return {"id": node_id, "links": links, "lines": lines, "body": body,
            "fm_end": fm_end, "links_start": links_start, "links_end": links_end}

files = sorted(glob.glob(os.path.join(KB, "knowledge", "**", "*.md"), recursive=True))
nodes = {}        # id -> parsed
malformed = []    # (path, reason)
dup_ids = []      # (id, path)

for path in files:
    p = parse(path)
    if "malformed" in p:
        malformed.append((path, p["malformed"]))
        continue
    p["path"] = path
    nid = p["id"]
    if nid in nodes:
        dup_ids.append((nid, path))
        continue
    nodes[nid] = p

# 去重(保序)+ 自链接检测
self_links = set()
desired = {}
deduped = set()
for nid, p in nodes.items():
    seen = []
    for x in p["links"]:
        if x == nid:
            self_links.add(nid)
        if x not in seen:
            seen.append(x)
    if seen != p["links"]:
        deduped.add(nid)
    desired[nid] = seen

# 正文 auto-link:只吸收已经存在的节点 id;悬空正文片段不报告,避免把普通文本误判为错误。
body_linked = []
node_id_re = re.compile(r"\b(?:person|project|area|org|event|decision|reading)-[A-Za-z0-9._-]+\b")
if AUTOLINK:
    all_ids = set(nodes)
    for nid, p in nodes.items():
        for candidate in sorted(set(node_id_re.findall(p["body"]))):
            if candidate == nid:
                continue
            if candidate in all_ids and candidate not in desired[nid]:
                desired[nid].append(candidate)
                body_linked.append((nid, candidate))

# 对称性 + 悬空(只基于原始边集迭代,补反向链接不产生级联)
dangling = []     # (A, B)
added = []        # (B, A) —— B 的 links 补回 A
for A in sorted(nodes):
    for B in list(desired[A]):
        if B == A:
            continue
        if B not in nodes:
            dangling.append((A, B))
            continue
        if A not in desired[B]:
            desired[B].append(A)
            added.append((B, A))

# 写回 dirty 节点(原子:temp-then-move;只改 links 块,body 逐字不变)
def rewrite(p, final):
    lines = p["lines"]
    block = ["links:"] + ["  - " + x for x in final]
    if p["links_start"] is not None:
        new = lines[:p["links_start"]] + block + lines[p["links_end"]:]
    else:
        new = lines[:p["fm_end"]] + block + lines[p["fm_end"]:]
    tmp = p["path"] + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(new))
    os.replace(tmp, p["path"])

changed = 0
for nid, p in nodes.items():
    if desired[nid] != p["links"]:
        changed += 1
        if not DRYRUN:
            rewrite(p, desired[nid])

# ── 报告 ──
mode_desc = []
if DRYRUN:
    mode_desc.append("dry-run,不写回")
if AUTOLINK:
    mode_desc.append("autolink")
print("byteworker · 双向链接校验" + (("(%s)" % ",".join(mode_desc)) if mode_desc else ""))
print("数据目录:%s" % KB)
print("扫描节点:%d" % len(nodes))

if malformed:
    print("\n✗ 格式异常(已跳过):%d" % len(malformed))
    for path, why in sorted(malformed):
        print("  · %s —— %s" % (os.path.relpath(path, KB), why))
if dup_ids:
    print("\n✗ 重复 id(后出现者已跳过):%d" % len(dup_ids))
    for nid, path in sorted(dup_ids):
        print("  · %s @ %s" % (nid, os.path.relpath(path, KB)))
if added:
    print("\n%s 补回的反向链接:%d" % ("→" if DRYRUN else "✓", len(added)))
    for B, A in sorted(added):
        print("  %s 的 links 补回 %s(因 %s → %s)" % (B, A, A, B))
if body_linked:
    print("\n%s 正文 auto-link:%d" % ("→" if DRYRUN else "✓", len(body_linked)))
    for A, B in sorted(body_linked):
        print("  %s 的 links 补入正文提及的 %s" % (A, B))
if deduped:
    print("\n%s 去重的节点:%d —— %s" % ("→" if DRYRUN else "✓", len(deduped), ", ".join(sorted(deduped))))
if self_links:
    print("\n✗ 自链接(只报告,未改):%d" % len(self_links))
    for nid in sorted(self_links):
        print("  · %s → %s" % (nid, nid))
if dangling:
    print("\n✗ 悬空链接(目标节点不存在,只报告,未改):%d" % len(dangling))
    for A, B in sorted(dangling):
        print("  · %s → %s" % (A, B))

if not (added or body_linked or deduped or self_links or dangling or malformed or dup_ids):
    print("\n✓ 链接图已对称、无异常。")
elif DRYRUN and changed:
    print("\n(dry-run:%d 个节点有可修复项未写回;去掉 --dry-run 实际执行。)" % changed)

print()
print("scanned=%d" % len(nodes))
print("malformed=%d" % len(malformed))
print("duplicate_ids=%d" % len(dup_ids))
print("backlinks_added=%d" % len(added))
print("body_links_added=%d" % len(body_linked))
print("deduped=%d" % len(deduped))
print("self_links=%d" % len(self_links))
print("dangling=%d" % len(dangling))
print("mode=%s" % ("dry-run" if DRYRUN else "apply"))

sys.exit(3 if dangling else 0)
PY
exit $?
