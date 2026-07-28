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
#   · 自链接   —— A 链到自己:确定性删除。
#   只动 frontmatter 的 links 块,其余 frontmatter 与 body 逐字不变。
#   不碰 git、不写 journal —— 由调用方按「写入规范」收尾。
#
# 核心逻辑在 bin/repair_links.py,本脚本只做参数解析与路径定位。
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

ARGS=("$KB")
[ "$DRYRUN" -eq 1 ] && ARGS+=(--dry-run)
[ "$AUTOLINK" -eq 1 ] && ARGS+=(--autolink)

python3 "$SELF_DIR/repair_links.py" "${ARGS[@]}"
exit $?
