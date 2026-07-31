#!/usr/bin/env bash
# byteworker · pull-chat.sh
# 拉取飞书群聊在某时间窗内的全部消息,逐字转写到文件,供知识库摄取(feishu_chat)使用。
#
# 用法:
#   bin/pull-chat.sh --query "<群名>"   --start <ISO8601> --end <ISO8601> [--out <file>] [--locators-out <json>]
#   bin/pull-chat.sh --chat-id <oc_xxx> --start <ISO8601> --end <ISO8601> [--out <file>] [--locators-out <json>]
#   bin/pull-chat.sh --query "<群名>"   --since-last [--kb <知识库目录>] [--end <ISO8601>] [--out <file>] [--locators-out <json>]
#
# --since-last:增量摄取。群聊是持续更新的消息流,同一个群通常反复多次摄取;
#   该参数让脚本自动「从上次摄取处续拉」—— 读 ../.kbconfig 定位知识库数据目录,
#   扫 raw_data/ 找该 chat_id 最近一次 feishu_chat 的 source_window 结束时间作 --start,
#   --end 缺省为当前时刻。该群在 raw_data/ 无历史摄取记录时退出码 4(应改用 --start 首次摄取)。
#
# 输出:
#   - 逐字转写写入 --out(缺省为 /tmp 临时文件);每条格式:
#       === [时间] 发送人 [open_id] (msg_type)
#       <内容>
#   - stdout 末尾打印摘要(供 agent 解析):
#       chat_id= / chat_name= / messages= / pages= / truncated= / window= / mode= / transcript= / locators=
# 退出码:0 成功 | 2 群未找到 | 3 匹配到多个群(需改用 --chat-id) | 4 --since-last 无历史窗口 | 5 拉取被页数上限截断 | 1 其他错误
set -uo pipefail

QUERY=""; CHAT_ID=""; START=""; END=""; OUT=""; LOCATORS_OUT=""; SINCE_LAST=""; KBDIR=""
PAGE_SIZE=50; OVERLAP_SECONDS=0
SELF_DIR=$(cd "$(dirname "$0")" && pwd)
KBCONFIG="$SELF_DIR/../.kbconfig"
LARK_CLI_BIN="${BYTEWORKER_LARK_CLI_BIN:-lark-cli}"
PYTHON_BIN="${BYTEWORKER_PYTHON_BIN:-python3}"
usage() { sed -n '2,21p' "$0"; }
while [ $# -gt 0 ]; do
  case "$1" in
    --query)      QUERY="${2:-}"; shift 2;;
    --chat-id)    CHAT_ID="${2:-}"; shift 2;;
    --start)      START="${2:-}"; shift 2;;
    --end)        END="${2:-}"; shift 2;;
    --kb)         KBDIR="${2:-}"; shift 2;;
    --page-size)  PAGE_SIZE="${2:-}"; shift 2;;
    --overlap-seconds) OVERLAP_SECONDS="${2:-}"; shift 2;;
    --out)        OUT="${2:-}"; shift 2;;
    --locators-out) LOCATORS_OUT="${2:-}"; shift 2;;
    --since-last) SINCE_LAST=1; shift;;
    -h|--help)    usage; exit 0;;
    *) echo "未知参数:$1" >&2; exit 1;;
  esac
done

case "$PAGE_SIZE" in
  ''|*[!0-9]*) echo "错误:--page-size 必须是正整数" >&2; exit 1;;
esac
[ "$PAGE_SIZE" -gt 0 ] || { echo "错误:--page-size 必须是正整数" >&2; exit 1; }
case "$OVERLAP_SECONDS" in
  ''|*[!0-9]*) echo "错误:--overlap-seconds 必须是非负整数" >&2; exit 1;;
esac

if [ -z "$QUERY" ] && [ -z "$CHAT_ID" ]; then
  echo "错误:--query(群名)或 --chat-id(oc_xxx)二选一" >&2; exit 1
fi
if [ -z "$SINCE_LAST" ] && { [ -z "$START" ] || [ -z "$END" ]; }; then
  echo "错误:--start 与 --end 必填(ISO8601,如 2026-04-21T00:00:00+08:00);或用 --since-last 增量摄取" >&2; exit 1
fi
command -v "$LARK_CLI_BIN" >/dev/null 2>&1 || { echo "错误:未找到 lark-cli" >&2; exit 1; }
command -v jq      >/dev/null 2>&1 || { echo "错误:未找到 jq" >&2; exit 1; }

CHAT_NAME=""

# 1. 按群名定位 chat_id（已给 --chat-id 则跳过）
if [ -z "$CHAT_ID" ]; then
  SRCH=$("$LARK_CLI_BIN" im +chat-search --query "$QUERY" 2>&1 || true)
  if ! echo "$SRCH" | jq -e '.ok == true' >/dev/null 2>&1; then
    echo "错误:chat-search 失败(可能未登录,试 lark-cli auth login):" >&2
    echo "$SRCH" | head -c 400 >&2; echo >&2; exit 1
  fi
  CNT=$(echo "$SRCH" | jq '.data.chats | length')
  if [ "$CNT" -eq 0 ]; then
    echo "未找到群:$QUERY" >&2; exit 2
  fi
  if [ "$CNT" -gt 1 ]; then
    echo "匹配到多个群,请改用 --chat-id 指定其一:" >&2
    echo "$SRCH" | jq -r '.data.chats[] | "  " + .chat_id + "  " + .name' >&2
    exit 3
  fi
  CHAT_ID=$(echo "$SRCH"  | jq -r '.data.chats[0].chat_id')
  CHAT_NAME=$(echo "$SRCH" | jq -r '.data.chats[0].name')
fi

# 1b. --since-last:扫 raw_data/ 推导增量起点
MODE="explicit"
if [ -n "$SINCE_LAST" ]; then
  MODE="since-last"
  [ -z "$END" ] && END=$(date "+%Y-%m-%dT%H:%M:%S+08:00")
  if [ -z "$START" ]; then
    if [ -z "$KBDIR" ]; then
      KBDIR=$(head -n1 "$KBCONFIG" 2>/dev/null | tr -d '[:space:]')
    fi
    LAST_END=""
    if [ -n "$KBDIR" ] && [ -d "$KBDIR/raw_data" ]; then
      for f in "$KBDIR"/raw_data/*.md; do
        [ -f "$f" ] || continue
        grep -Eq "^source_chat_id:[[:space:]]*${CHAT_ID}[[:space:]]*\$" "$f" || continue
        if ! e=$("$PYTHON_BIN" - "$SELF_DIR/../lib" "$f" <<'PY'
from datetime import datetime
import sys

sys.path.insert(0, sys.argv[1])
from frontmatter import parse_file, source_window_end

frontmatter, _ = parse_file(sys.argv[2])
value = source_window_end(str(frontmatter.get("source_window", "")))
if not value:
    raise SystemExit(0)
if "T" not in value:
    value = f"{value}T00:00:00+08:00"
try:
    datetime.fromisoformat(
        value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    )
except ValueError:
    print(
        f"错误:无法解析 {sys.argv[2]} 的 source_window 结束时间",
        file=sys.stderr,
    )
    raise SystemExit(2)
print(value)
PY
); then
          exit 1
        fi
        [ -n "$e" ] || continue
        if [ -z "$LAST_END" ] || [[ "$e" > "$LAST_END" ]]; then LAST_END="$e"; fi
      done
    fi
    if [ -z "$LAST_END" ]; then
      echo "错误:--since-last 但该群($CHAT_ID)在 raw_data/ 无历史摄取记录;首次摄取请用 --start 指定起点" >&2
      exit 4
    fi
    START="$LAST_END"
    if [ "$OVERLAP_SECONDS" -gt 0 ]; then
      START=$("$PYTHON_BIN" - "$START" "$OVERLAP_SECONDS" <<'PY'
from datetime import datetime, timedelta
import sys

value = sys.argv[1]
parsed = datetime.fromisoformat(
    value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
)
print((parsed - timedelta(seconds=int(sys.argv[2]))).isoformat())
PY
)
    fi
  fi
fi

# 输出文件
if [ -z "$OUT" ]; then OUT=$(mktemp /tmp/byteworker-chat-XXXXXX); fi
: > "$OUT"
if [ -z "$LOCATORS_OUT" ]; then
  LOCATORS_OUT=$(mktemp /tmp/byteworker-chat-locators-XXXXXX.json)
fi

# 2. 分页拉取消息(stdout/stderr 分离,避免污染 JSON)
TMP=$(mktemp); TMPERR=$(mktemp); TMPLOC=$(mktemp)
trap 'rm -f "$TMP" "$TMPERR" "$TMPLOC"' EXIT
TOKEN=""; PAGE=0; TOTAL=0; TRUNCATED=0
MAX_PAGES="${BYTEWORKER_CHAT_MAX_PAGES:-60}"
while :; do
  PAGE=$((PAGE+1))
  if [ -z "$TOKEN" ]; then
    "$LARK_CLI_BIN" im +chat-messages-list --chat-id "$CHAT_ID" --start "$START" --end "$END" \
      --sort asc --page-size "$PAGE_SIZE" >"$TMP" 2>"$TMPERR" || true
  else
    "$LARK_CLI_BIN" im +chat-messages-list --chat-id "$CHAT_ID" --start "$START" --end "$END" \
      --sort asc --page-size "$PAGE_SIZE" --page-token "$TOKEN" >"$TMP" 2>"$TMPERR" || true
  fi
  if ! jq -e '.ok == true' "$TMP" >/dev/null 2>&1; then
    echo "错误:chat-messages-list 第 $PAGE 页失败:" >&2
    head -c 300 "$TMP" >&2; head -c 300 "$TMPERR" >&2; echo >&2; exit 1
  fi
  N=$(jq '.data.messages | length' "$TMP")
  TOTAL=$((TOTAL + N))
  jq -r '
    def sender_open_id: (.sender.id // .sender.open_id // .sender.sender_id.open_id // "");
    .data.messages[]
    | "=== [" + .create_time + "] "
      + (.sender.name // "系统")
      + (if sender_open_id != "" then " [" + sender_open_id + "]" else "" end)
      + " (" + .msg_type + ")\n"
      + (.content // "")
  ' "$TMP" >> "$OUT"
  jq -c --arg chat_id "$CHAT_ID" '
    .data.messages[]
    | (.message_id // .id // "") as $message_id
    | select($message_id != "")
    | {
        anchor_id:("chat:message:" + $message_id),
        kind:"chat_message",
        precision:"exact",
        open_url:(.message_url // .url // ""),
        source_time:(.create_time // ""),
        author:(.sender.name // ""),
        quote:((.content // "") | tostring | gsub("[\\r\\n\\t]+"; " ") | .[0:240]),
        locator:{
          chat_id:(.chat_id // $chat_id),
          message_id:$message_id,
          thread_id:(.thread_id // "")
        }
      }
  ' "$TMP" >> "$TMPLOC"
  HAS=$(jq -r '.data.has_more' "$TMP")
  TOKEN=$(jq -r '.data.page_token // ""' "$TMP")
  [ "$HAS" = "true" ] || break
  if [ "$PAGE" -ge "$MAX_PAGES" ]; then
    echo "错误:达 ${MAX_PAGES} 页上限,本次窗口可能未拉全;请缩小 --start/--end 后重试" >&2
    TRUNCATED=1
    break
  fi
done

jq -s \
  --arg chat_id "$CHAT_ID" \
  --arg window "$START .. $END" \
  '{
    schema_version:"byteworker-source-locators/v1",
    source_type:"feishu_chat",
    source_chat_id:$chat_id,
    source_window:$window,
    anchors:.
  }' "$TMPLOC" > "$LOCATORS_OUT"

# 3. 摘要(供 agent 解析)
echo "chat_id=$CHAT_ID"
echo "chat_name=${CHAT_NAME:-$QUERY}"
echo "messages=$TOTAL"
echo "pages=$PAGE"
echo "truncated=$TRUNCATED"
echo "window=$START .. $END"
echo "mode=$MODE"
echo "page_size=$PAGE_SIZE"
echo "overlap_seconds=$OVERLAP_SECONDS"
echo "transcript=$OUT"
echo "locators=$LOCATORS_OUT"
if [ "$TRUNCATED" -eq 1 ]; then
  exit 5
fi
exit 0
