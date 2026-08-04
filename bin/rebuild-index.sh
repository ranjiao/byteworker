#!/usr/bin/env bash
# byteworker · rebuild-index.sh
# 从 knowledge/ 节点、sources/ profiles 与 raw_data/ frontmatter 确定性重建 INDEX.md。
#
# 用法:
#   bin/rebuild-index.sh [--dry-run] [--kb <数据目录>]
#   bin/rebuild-index.sh --help
#
#   --dry-run   只把生成结果输出到 stdout,不写回 INDEX.md。
#   --kb <dir>  指定知识库数据目录,覆盖 .kbconfig(主要用于测试)。
#
# 做什么:
#   · 扫 8 类 knowledge 节点,生成节点登记表;人员表带 feishu_id 列。
#   · 优先扫 sources/ profiles，再兼容带 routine 的 raw_data，生成「定期摄取清单」。
#   · 扫 feishu_chat raw_data,生成「群聊摄取进度」高水位。
#   · 原子写回 INDEX.md;不碰 git、不写 journal —— 由调用方按「写入规范」收尾。
#
# 核心逻辑在 bin/rebuild_index.py,本脚本只做参数解析与路径定位。
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
PYTHON_BIN="${BYTEWORKER_PYTHON_BIN:-python3}"
command -v "$PYTHON_BIN" >/dev/null 2>&1 || { echo "错误:未找到可用 Python" >&2; exit 1; }

ARGS=("$KB")
[ "$DRYRUN" -eq 1 ] && ARGS+=(--dry-run)

"$PYTHON_BIN" "$SELF_DIR/rebuild_index.py" "${ARGS[@]}"
exit $?
