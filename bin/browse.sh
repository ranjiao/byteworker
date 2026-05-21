#!/usr/bin/env bash
# byteworker · browse.sh
# 起一个本地静态服务器,用 skill 自带的 viewer 浏览知识库的全部 md 节点。
#
# 用法:
#   bin/browse.sh [port]      # port 缺省 8765
#
# 做什么:
#   1. 读 ../.kbconfig 定位知识库数据目录;
#   2. 建一个临时服务根目录,里面只放两个符号链接:
#        app/ → 本 skill 的 viewer/(viewer 代码随 skill 分发)
#        kb/  → 知识库数据目录(只读浏览,绝不写入)
#   3. 在临时根起 `python3 -m http.server`(纯静态文件服务器,零自定义后端);
#   4. 打开浏览器到 viewer 页面。Ctrl-C 停止,临时目录自动清理。
#
# viewer 是纯前端、只读 —— 浏览/检索/沿 links 跳转;编辑知识库仍走 byteworker skill。
# 关键:viewer 代码始终在 skill 仓库内,数据目录一个字节都不写入。
set -euo pipefail

SELF_DIR=$(cd "$(dirname "$0")" && pwd)
SKILL_DIR=$(cd "$SELF_DIR/.." && pwd)
KBCONFIG="$SKILL_DIR/.kbconfig"
PORT="${1:-8765}"

[ -f "$KBCONFIG" ] || { echo "错误:未找到 .kbconfig(byteworker 尚未首次配置)" >&2; exit 1; }
KBDIR=$(head -n1 "$KBCONFIG" | tr -d '[:space:]')
[ -n "$KBDIR" ] && [ -d "$KBDIR" ] || { echo "错误:知识库数据目录不存在:$KBDIR" >&2; exit 1; }
[ -f "$KBDIR/INDEX.md" ] || { echo "错误:$KBDIR 下没有 INDEX.md,似乎不是知识库数据目录" >&2; exit 1; }
[ -d "$SKILL_DIR/viewer" ] || { echo "错误:未找到 $SKILL_DIR/viewer(skill 不完整)" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "错误:未找到 python3" >&2; exit 1; }

# 临时服务根:只含两个符号链接,退出时自动清理 —— 不在数据目录里留任何东西
SERVE_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/byteworker-viewer.XXXXXX")
trap 'rm -rf "$SERVE_ROOT"' EXIT INT TERM
ln -s "$SKILL_DIR/viewer" "$SERVE_ROOT/app"
ln -s "$KBDIR" "$SERVE_ROOT/kb"

URL="http://localhost:$PORT/app/index.html"
echo "byteworker viewer → $URL"
echo "(静态服务器,纯本地;Ctrl-C 停止)"

# 1 秒后开浏览器(等服务器起来)
( sleep 1
  if command -v open >/dev/null 2>&1; then open "$URL"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL"
  fi ) &

# 不用 exec —— 保留 trap,python 退出后能清理临时目录
cd "$SERVE_ROOT"
python3 -m http.server "$PORT" --bind 127.0.0.1
