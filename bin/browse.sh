#!/usr/bin/env bash
# byteworker · browse.sh
# 起一个本地静态服务器,用 skill 自带的 viewer 浏览知识库的全部 md 节点。
#
# 用法:
#   bin/browse.sh [port]      # port 缺省 8765;被占用时自动换到其他可用端口
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

case "$PORT" in
  ''|*[!0-9]*)
    echo "错误:端口必须是 1-65535 之间的整数:$PORT" >&2
    exit 1
    ;;
esac
if [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
  echo "错误:端口必须是 1-65535 之间的整数:$PORT" >&2
  exit 1
fi

[ -f "$KBCONFIG" ] || { echo "错误:未找到 .kbconfig(byteworker 尚未首次配置)" >&2; exit 1; }
KBDIR=$(head -n1 "$KBCONFIG" | tr -d '[:space:]')
[ -n "$KBDIR" ] && [ -d "$KBDIR" ] || { echo "错误:知识库数据目录不存在:$KBDIR" >&2; exit 1; }
[ -f "$KBDIR/INDEX.md" ] || { echo "错误:$KBDIR 下没有 INDEX.md,似乎不是知识库数据目录" >&2; exit 1; }
[ -d "$SKILL_DIR/viewer" ] || { echo "错误:未找到 $SKILL_DIR/viewer(skill 不完整)" >&2; exit 1; }
PYTHON_BIN="${BYTEWORKER_PYTHON_BIN:-python3}"
command -v "$PYTHON_BIN" >/dev/null 2>&1 || { echo "错误:未找到可用 Python" >&2; exit 1; }

# 从请求端口开始寻找可绑定的端口;到 65535 后从 1024 继续查找。
# 用 Python 探测以避免依赖 lsof / nc(不同系统的参数并不一致)。
REQUESTED_PORT="$PORT"
PORT=$("$PYTHON_BIN" - "$REQUESTED_PORT" <<'PY'
import socket
import sys

requested = int(sys.argv[1])
candidates = range(requested, 65536)
if requested > 1024:
    candidates = (*candidates, *range(1024, requested))

for port in candidates:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            continue
    print(port)
    break
else:
    raise SystemExit("没有找到可用的本地端口")
PY
)

if [ "$PORT" != "$REQUESTED_PORT" ]; then
  echo "端口 $REQUESTED_PORT 已被占用,自动改用 $PORT"
fi

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
"$PYTHON_BIN" -m http.server "$PORT" --bind 127.0.0.1
