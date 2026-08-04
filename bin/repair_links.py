#!/usr/bin/env python3
"""
byteworker · repair-links
校验并修复知识库节点间双向链接(links)的对称性。

用法:
  python3 repair_links.py <kb_dir> [--dry-run] [--autolink]

退出码: 0 成功(干净/已修复) | 1 环境或参数错误 | 3 完成但存在悬空链接需人工复核
"""
import sys
import os
import glob
import re


def _add_lib_path():
    self_dir = os.path.dirname(os.path.abspath(__file__))
    lib_dir = os.path.join(self_dir, "..", "lib")
    lib_dir = os.path.normpath(lib_dir)
    if lib_dir not in sys.path:
        sys.path.insert(0, lib_dir)


_add_lib_path()


def parse_node(path):
    """
    解析一个节点文件，返回结构化信息。
    比普通 frontmatter 解析多了行号追踪，用于原地修改 links 块。
    """
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
            key = key.strip()
            val = val.strip()
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
    return {
        "id": node_id,
        "links": links,
        "lines": lines,
        "body": body,
        "fm_end": fm_end,
        "links_start": links_start,
        "links_end": links_end,
    }


def rewrite_node(p, final_links):
    """重写节点的 links 块，其余内容逐字不变。"""
    lines = p["lines"]
    block = ["links:"] + ["  - " + x for x in final_links]
    if p["links_start"] is not None:
        new = lines[:p["links_start"]] + block + lines[p["links_end"]:]
    else:
        new = lines[:p["fm_end"]] + block + lines[p["fm_end"]:]
    tmp = p["path"] + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(new))
    os.replace(tmp, p["path"])


def main():
    if len(sys.argv) < 2:
        print("用法: repair_links.py <kb_dir> [--dry-run] [--autolink]", file=sys.stderr)
        sys.exit(1)

    kb = sys.argv[1]
    dry_run = "--dry-run" in sys.argv
    autolink = "--autolink" in sys.argv

    if not os.path.isdir(kb):
        print("错误: 知识库数据目录不存在: %s" % kb, file=sys.stderr)
        sys.exit(1)
    if not os.path.isdir(os.path.join(kb, "knowledge")):
        print("错误: %s 下没有 knowledge/, 似乎不是知识库数据目录" % kb, file=sys.stderr)
        sys.exit(1)

    files = sorted(glob.glob(os.path.join(kb, "knowledge", "**", "*.md"), recursive=True))
    nodes = {}
    malformed = []
    dup_ids = []

    for path in files:
        p = parse_node(path)
        if "malformed" in p:
            malformed.append((path, p["malformed"]))
            continue
        p["path"] = path
        nid = p["id"]
        if nid in nodes:
            dup_ids.append((nid, path))
            continue
        nodes[nid] = p

    # 去重(保序) + 删除自链接
    self_links = set()
    desired = {}
    deduped = set()
    for nid, p in nodes.items():
        seen = []
        for x in p["links"]:
            if x == nid:
                self_links.add(nid)
                continue
            if x not in seen:
                seen.append(x)
        if seen != p["links"]:
            deduped.add(nid)
        desired[nid] = seen

    # 正文 auto-link: 只吸收已经存在的节点 id
    body_linked = []
    node_id_re = re.compile(
        r"\b(?:person|project|area|org|event|decision|reading|thinking)-"
        r"[A-Za-z0-9._-]+\b"
    )
    if autolink:
        all_ids = set(nodes)
        for nid, p in nodes.items():
            for candidate in sorted(set(node_id_re.findall(p["body"]))):
                if candidate == nid:
                    continue
                if candidate in all_ids and candidate not in desired[nid]:
                    desired[nid].append(candidate)
                    body_linked.append((nid, candidate))

    # 对称性 + 悬空(只基于原始边集迭代, 补反向链接不产生级联)
    dangling = []
    added = []
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

    # 写回 dirty 节点
    changed = 0
    for nid, p in nodes.items():
        if desired[nid] != p["links"]:
            changed += 1
            if not dry_run:
                rewrite_node(p, desired[nid])

    # ── 报告 ──
    mode_desc = []
    if dry_run:
        mode_desc.append("dry-run, 不写回")
    if autolink:
        mode_desc.append("autolink")
    print("byteworker · 双向链接校验" + (("(%s)" % ",".join(mode_desc)) if mode_desc else ""))
    print("数据目录: %s" % kb)
    print("扫描节点: %d" % len(nodes))

    if malformed:
        print("\n✗ 格式异常(已跳过): %d" % len(malformed))
        for path, why in sorted(malformed):
            print("  · %s —— %s" % (os.path.relpath(path, kb), why))
    if dup_ids:
        print("\n✗ 重复 id(后出现者已跳过): %d" % len(dup_ids))
        for nid, path in sorted(dup_ids):
            print("  · %s @ %s" % (nid, os.path.relpath(path, kb)))
    if added:
        print("\n%s 补回的反向链接: %d" % ("→" if dry_run else "✓", len(added)))
        for B, A in sorted(added):
            print("  %s 的 links 补回 %s(因 %s → %s)" % (B, A, A, B))
    if body_linked:
        print("\n%s 正文 auto-link: %d" % ("→" if dry_run else "✓", len(body_linked)))
        for A, B in sorted(body_linked):
            print("  %s 的 links 补入正文提及的 %s" % (A, B))
    if deduped:
        print("\n%s 去重的节点: %d —— %s" % ("→" if dry_run else "✓", len(deduped), ", ".join(sorted(deduped))))
    if self_links:
        print("\n%s 删除的自链接: %d" % ("→" if dry_run else "✓", len(self_links)))
        for nid in sorted(self_links):
            print("  · %s → %s" % (nid, nid))
    if dangling:
        print("\n✗ 悬空链接(目标节点不存在, 只报告, 未改): %d" % len(dangling))
        for A, B in sorted(dangling):
            print("  · %s → %s" % (A, B))

    if not (added or body_linked or deduped or self_links or dangling or malformed or dup_ids):
        print("\n✓ 链接图已对称、无异常。")
    elif dry_run and changed:
        print("\n(dry-run: %d 个节点有可修复项未写回; 去掉 --dry-run 实际执行。)" % changed)

    print()
    print("scanned=%d" % len(nodes))
    print("malformed=%d" % len(malformed))
    print("duplicate_ids=%d" % len(dup_ids))
    print("backlinks_added=%d" % len(added))
    print("body_links_added=%d" % len(body_linked))
    print("deduped=%d" % len(deduped))
    print("self_links=%d" % len(self_links))
    print("dangling=%d" % len(dangling))
    print("mode=%s" % ("dry-run" if dry_run else "apply"))

    sys.exit(3 if dangling else 0)


if __name__ == "__main__":
    main()
