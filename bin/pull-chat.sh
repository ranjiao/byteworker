#!/usr/bin/env bash
# byteworker · pull-chat.sh
# 拉取飞书群聊在某时间窗内的全部消息,逐字转写到文件,供知识库摄取(feishu_chat)使用。
#
# 用法:
#   bin/pull-chat.sh --query "<群名>"   --start <ISO8601> --end <ISO8601> [--out <file>]
#   bin/pull-chat.sh --chat-id <oc_xxx> --start <ISO8601> --end <ISO8601> [--out <file>]
#
# 输出:
#   - 逐字转写写入 --out(缺省为 /tmp 临时文件);每条格式:
#       === [时间] 发送人 (msg_type)
#       <内容>
#   - stdout 末尾打印摘要(供 agent 解析):
#       chat_id= / chat_name= / messages= / pages= / window= / transcript=
# 退出码:0 成功 | 2 群未找到 | 3 匹配到多个群(需改用 --chat-id) | 1 其他错误
set -uo pipefail

QUERY=""; CHAT_ID=""; START=""; END=""; OUT=""
usage() { sed -n '2,18p' "$0"; }
while [ $# -gt 0 ]; do
  case "$1" in
    --query)   QUERY="${2:-}"; shift 2;;
    --chat-id) CHAT_ID="${2:-}"; shift 2;;
    --start)   START="${2:-}"; shift 2;;
    --end)     END="${2:-}"; shift 2;;
    --out)     OUT="${2:-}"; shift 2;;
    -h|--help) usage; exit 0;;
    *) echo "未知参数:$1" >&2; exit 1;;
  esac
done

if [ -z "$START" ] || [ -z "$END" ]; then
  echo "错误:--start 与 --end 必填(ISO8601,如 2026-04-21T00:00:00+08:00)" >&2; exit 1
fi
if [ -z "$QUERY" ] && [ -z "$CHAT_ID" ]; then
  echo "错误:--query(群名)或 --chat-id(oc_xxx)二选一" >&2; exit 1
fi
command -v lark-cli >/dev/null 2>&1 || { echo "错误:未找到 lark-cli" >&2; exit 1; }
command -v jq      >/dev/null 2>&1 || { echo "错误:未找到 jq" >&2; exit 1; }

CHAT_NAME=""

# 1. 按群名定位 chat_id（已给 --chat-id 则跳过）
if [ -z "$CHAT_ID" ]; then
  SRCH=$(lark-cli im +chat-search --query "$QUERY" 2>&1 || true)
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

# 输出文件
if [ -z "$OUT" ]; then OUT=$(mktemp /tmp/byteworker-chat-XXXXXX); fi
: > "$OUT"

# 2. 分页拉取消息(stdout/stderr 分离,避免污染 JSON)
TMP=$(mktemp); TMPERR=$(mktemp)
trap 'rm -f "$TMP" "$TMPERR"' EXIT
TOKEN=""; PAGE=0; TOTAL=0
while :; do
  PAGE=$((PAGE+1))
  if [ -z "$TOKEN" ]; then
    lark-cli im +chat-messages-list --chat-id "$CHAT_ID" --start "$START" --end "$END" \
      --sort asc --page-size 50 >"$TMP" 2>"$TMPERR" || true
  else
    lark-cli im +chat-messages-list --chat-id "$CHAT_ID" --start "$START" --end "$END" \
      --sort asc --page-size 50 --page-token "$TOKEN" >"$TMP" 2>"$TMPERR" || true
  fi
  if ! jq -e '.ok == true' "$TMP" >/dev/null 2>&1; then
    echo "错误:chat-messages-list 第 $PAGE 页失败:" >&2
    head -c 300 "$TMP" >&2; head -c 300 "$TMPERR" >&2; echo >&2; exit 1
  fi
  N=$(jq '.data.messages | length' "$TMP")
  TOTAL=$((TOTAL + N))
  jq -r '.data.messages[] | "=== [" + .create_time + "] " + (.sender.name // "系统") + " (" + .msg_type + ")\n" + (.content // "")' "$TMP" >> "$OUT"
  HAS=$(jq -r '.data.has_more' "$TMP")
  TOKEN=$(jq -r '.data.page_token // ""' "$TMP")
  [ "$HAS" = "true" ] || break
  if [ "$PAGE" -ge 60 ]; then echo "警告:达 60 页上限,停止(可能未拉全)" >&2; break; fi
done

# 3. 摘要(供 agent 解析)
echo "chat_id=$CHAT_ID"
echo "chat_name=${CHAT_NAME:-$QUERY}"
echo "messages=$TOTAL"
echo "pages=$PAGE"
echo "window=$START .. $END"
echo "transcript=$OUT"
