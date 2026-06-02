#!/usr/bin/env bash
# byteworker · im-inbox-summary.sh
# 发现最近一个时间窗内 IM 里的高信号 discussion threads,输出 JSON 候选集。
#
# 这个脚本只做本地粗筛:拉取候选会话、规范化消息、按关键词/context/INDEX 轻量打分、
# 聚合成候选 thread。LLM 精判、日报/周报写入、必要时 digest 入库由 skill 层继续处理。
#
# 用法:
#   bin/im-inbox-summary.sh [--last-hours 24] [--kb <dir>] [--out <file>]
#   bin/im-inbox-summary.sh --today [--kb <dir>] [--out <file>]
#   bin/im-inbox-summary.sh --start <ISO8601> --end <ISO8601> [--chat-id oc_xxx]
#   可重复追加 --keyword <关键词>;首次运行会提示用户补充重点项目/人名/业务词。
#   命令较重,建议一天运行一次;同一天或短时间重复运行会提示收益很低。
#
# 退出码:0 成功 | 1 参数/环境错误 | 2 lark-cli 拉取失败但已降级输出候选为空
set -uo pipefail

SELF_DIR=$(cd "$(dirname "$0")" && pwd)
KBCONFIG="$SELF_DIR/../.kbconfig"

START=""
END=""
TODAY=""
LAST_HOURS=24
KBDIR=""
OUT=""
DRY_RUN=""
NO_CHAT_LIST=""
NO_SEARCH=""
QUERYLESS_SEARCH=""
NO_CONTEXT_SEARCH=""
NO_FIRST_RUN_NOTICE=""
NO_REPEAT_RUN_NOTICE=""
REPEAT_NOTICE_HOURS=20

MAX_CHATS=30
PER_CHAT_LIMIT=200
GLOBAL_MESSAGE_LIMIT=3000
MAX_CANDIDATE_THREADS=300
MAX_LLM_THREADS=80
THREAD_GAP_MINUTES=10
REPRESENTATIVE_LIMIT=8
PAGE_SIZE=50
SEARCH_PAGE_LIMIT=2
SEARCH_KEYWORDS_LIMIT=12
CONTEXT_SEARCH_LIMIT=12
MIN_SCORE=3

EXTRA_KEYWORDS=()
FORCED_CHAT_IDS=()

usage() { sed -n '2,13p' "$0"; }

append_csv_values() {
  local raw="$1"
  local which="$2"
  local old_ifs="$IFS"
  IFS=','
  for item in $raw; do
    item=$(printf '%s' "$item" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    [ -n "$item" ] || continue
    if [ "$which" = "chat" ]; then
      FORCED_CHAT_IDS+=("$item")
    else
      EXTRA_KEYWORDS+=("$item")
    fi
  done
  IFS="$old_ifs"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --start) START="${2:-}"; shift 2;;
    --end) END="${2:-}"; shift 2;;
    --today) TODAY=1; shift;;
    --last-hours) LAST_HOURS="${2:-}"; shift 2;;
    --kb) KBDIR="${2:-}"; shift 2;;
    --out) OUT="${2:-}"; shift 2;;
    --chat-id) append_csv_values "${2:-}" chat; shift 2;;
    --keyword) append_csv_values "${2:-}" keyword; shift 2;;
    --max-chats) MAX_CHATS="${2:-}"; shift 2;;
    --per-chat-limit) PER_CHAT_LIMIT="${2:-}"; shift 2;;
    --global-message-limit) GLOBAL_MESSAGE_LIMIT="${2:-}"; shift 2;;
    --max-candidate-threads) MAX_CANDIDATE_THREADS="${2:-}"; shift 2;;
    --max-llm-threads) MAX_LLM_THREADS="${2:-}"; shift 2;;
    --thread-gap-minutes) THREAD_GAP_MINUTES="${2:-}"; shift 2;;
    --representative-limit) REPRESENTATIVE_LIMIT="${2:-}"; shift 2;;
    --page-size) PAGE_SIZE="${2:-}"; shift 2;;
    --search-page-limit) SEARCH_PAGE_LIMIT="${2:-}"; shift 2;;
    --search-keywords-limit) SEARCH_KEYWORDS_LIMIT="${2:-}"; shift 2;;
    --context-search-limit) CONTEXT_SEARCH_LIMIT="${2:-}"; shift 2;;
    --min-score) MIN_SCORE="${2:-}"; shift 2;;
    --no-chat-list) NO_CHAT_LIST=1; shift;;
    --no-search) NO_SEARCH=1; shift;;
    --queryless-search) QUERYLESS_SEARCH=1; shift;;
    --no-context-search) NO_CONTEXT_SEARCH=1; shift;;
    --no-first-run-notice) NO_FIRST_RUN_NOTICE=1; shift;;
    --no-repeat-run-notice) NO_REPEAT_RUN_NOTICE=1; shift;;
    --repeat-notice-hours) REPEAT_NOTICE_HOURS="${2:-}"; shift 2;;
    --dry-run) DRY_RUN=1; shift;;
    -h|--help) usage; exit 0;;
    *) echo "未知参数:$1" >&2; exit 1;;
  esac
done

is_positive_int() {
  printf '%s' "$1" | grep -Eq '^[0-9]+$' && [ "$1" -gt 0 ]
}

for pair in \
  "LAST_HOURS:$LAST_HOURS" \
  "MAX_CHATS:$MAX_CHATS" \
  "PER_CHAT_LIMIT:$PER_CHAT_LIMIT" \
  "GLOBAL_MESSAGE_LIMIT:$GLOBAL_MESSAGE_LIMIT" \
  "MAX_CANDIDATE_THREADS:$MAX_CANDIDATE_THREADS" \
  "MAX_LLM_THREADS:$MAX_LLM_THREADS" \
  "THREAD_GAP_MINUTES:$THREAD_GAP_MINUTES" \
  "REPRESENTATIVE_LIMIT:$REPRESENTATIVE_LIMIT" \
  "PAGE_SIZE:$PAGE_SIZE" \
  "SEARCH_PAGE_LIMIT:$SEARCH_PAGE_LIMIT" \
  "SEARCH_KEYWORDS_LIMIT:$SEARCH_KEYWORDS_LIMIT" \
  "CONTEXT_SEARCH_LIMIT:$CONTEXT_SEARCH_LIMIT" \
  "MIN_SCORE:$MIN_SCORE" \
  "REPEAT_NOTICE_HOURS:$REPEAT_NOTICE_HOURS"; do
  name=${pair%%:*}; value=${pair#*:}
  is_positive_int "$value" || { echo "错误:$name 必须是正整数" >&2; exit 1; }
done

iso_now() {
  date '+%Y-%m-%dT%H:%M:%S%z' | sed 's/\([+-][0-9][0-9]\)\([0-9][0-9]\)$/\1:\2/'
}

iso_today_start() {
  date '+%Y-%m-%dT00:00:00%z' | sed 's/\([+-][0-9][0-9]\)\([0-9][0-9]\)$/\1:\2/'
}

iso_hours_ago() {
  local hours="$1"
  if date -v-"${hours}"H '+%Y-%m-%dT%H:%M:%S%z' >/dev/null 2>&1; then
    date -v-"${hours}"H '+%Y-%m-%dT%H:%M:%S%z' | sed 's/\([+-][0-9][0-9]\)\([0-9][0-9]\)$/\1:\2/'
  else
    date -d "${hours} hours ago" '+%Y-%m-%dT%H:%M:%S%z' | sed 's/\([+-][0-9][0-9]\)\([0-9][0-9]\)$/\1:\2/'
  fi
}

if [ -n "$TODAY" ]; then
  START=$(iso_today_start)
  END=$(iso_now)
else
  [ -z "$END" ] && END=$(iso_now)
  [ -z "$START" ] && START=$(iso_hours_ago "$LAST_HOURS")
fi

if [ -z "$KBDIR" ] && [ -f "$KBCONFIG" ]; then
  KBDIR=$(head -n1 "$KBCONFIG" 2>/dev/null | tr -d '[:space:]')
fi

TMPDIR=$(mktemp -d /tmp/byteworker-im-inbox-XXXXXX)
WARN_FILE="$TMPDIR/warnings.txt"
TERMS_LINES="$TMPDIR/context-terms.txt"
TERMS_JSON="$TMPDIR/context-terms.json"
KEYWORDS_JSON="$TMPDIR/keywords.json"
ACTION_JSON="$TMPDIR/action.json"
RISK_JSON="$TMPDIR/risk.json"
EVIDENCE_JSON="$TMPDIR/evidence.json"
NOISE_JSON="$TMPDIR/noise.json"
CHAT_CANDIDATES="$TMPDIR/chat-candidates.jsonl"
CHAT_JSON="$TMPDIR/chats.json"
MESSAGES_JSONL="$TMPDIR/messages.jsonl"
TRUNCATED_CHATS="$TMPDIR/truncated-chats.txt"
RESULT_JSON="$TMPDIR/result.json"
FIRST_RUN_NOTICE_FILE="$TMPDIR/first-run-notice.txt"
REPEAT_RUN_NOTICE_FILE="$TMPDIR/repeat-run-notice.txt"
trap 'rm -rf "$TMPDIR"' EXIT

: > "$WARN_FILE"
: > "$TERMS_LINES"
: > "$CHAT_CANDIDATES"
: > "$MESSAGES_JSONL"
: > "$TRUNCATED_CHATS"
: > "$FIRST_RUN_NOTICE_FILE"
: > "$REPEAT_RUN_NOTICE_FILE"

warn() {
  printf '%s\n' "$*" >&2
  printf '%s\n' "$*" >> "$WARN_FILE"
}

json_array_from_lines() {
  awk 'NF {print}' | awk '!seen[$0]++' | jq -R -s 'split("\n") | map(select(length > 0))'
}

json_string() {
  jq -Rn --arg v "$1" '$v'
}

FIRST_RUN_MARKER="$SELF_DIR/../.im-inbox-summary-first-run-shown"
FIRST_RUN_NOTICE_SHOWN=false
LAST_RUN_MARKER="$SELF_DIR/../.im-inbox-summary-last-run.json"
REPEAT_RUN_NOTICE_SHOWN=false

write_first_run_notice_text() {
  cat > "$FIRST_RUN_NOTICE_FILE" <<'EOF'
IM Inbox Summary 首次运行说明:
- 我会扫描指定时间窗内的最近活跃会话、@我消息、内置高信号关键词,并结合 context.md / INDEX.md 里的项目、人、组织、群聊等词表做召回。
- 我会先在本地降噪、打分、按 10 分钟左右的时间窗口聚成 discussion threads;只把高信号候选交给后续 LLM 精判。
- 默认不会把全天 IM 原文写入 raw_data/ 或知识库;只有明确决策、项目状态变化、关键风险、重要跨团队对齐才建议重新拉小窗口 digest 入库。
- 为了减少漏召回,请补充你希望特别关注的关键词,比如重点项目名、业务线、人名、组织名、群名、指标名、风险词。可以在命令里重复使用 --keyword <词>,也可以把长期关注项维护到 context.md。
EOF
}

show_first_run_notice_if_needed() {
  [ -z "$NO_FIRST_RUN_NOTICE" ] || return
  [ -z "$DRY_RUN" ] || return
  [ -f "$FIRST_RUN_MARKER" ] && return
  write_first_run_notice_text
  cat "$FIRST_RUN_NOTICE_FILE" >&2
  printf '\n' >&2
  : > "$FIRST_RUN_MARKER" 2>/dev/null || warn "无法写入首次运行提示标记:$FIRST_RUN_MARKER"
  FIRST_RUN_NOTICE_SHOWN=true
}

write_repeat_run_notice_text() {
  local last_run_at="$1"
  local last_window="$2"
  cat > "$REPEAT_RUN_NOTICE_FILE" <<EOF
IM Inbox Summary 重复运行提醒:
- 这个命令会扫描 IM 会话并拉取消息,成本比较高,建议一天最多运行一次。
- 上次运行时间: ${last_run_at:-未知};上次窗口: ${last_window:-未知}。
- 短时间重复运行通常不会带来新信息,还可能增加处理时间和噪音。除非你刚补充了关键词、更新了 context.md,或确实有新的重要聊天,否则可以等到明天再跑。
EOF
}

show_repeat_run_notice_if_needed() {
  [ -z "$NO_REPEAT_RUN_NOTICE" ] || return
  [ -z "$DRY_RUN" ] || return
  [ -f "$LAST_RUN_MARKER" ] || return

  local now_epoch today last_epoch last_date last_run_at last_window elapsed threshold
  now_epoch=$(date '+%s')
  today=$(date '+%Y-%m-%d')
  last_epoch=$(jq -r '.last_run_epoch // empty' "$LAST_RUN_MARKER" 2>/dev/null || true)
  last_date=$(jq -r '.run_date // empty' "$LAST_RUN_MARKER" 2>/dev/null || true)
  last_run_at=$(jq -r '.last_run_at // empty' "$LAST_RUN_MARKER" 2>/dev/null || true)
  last_window=$(jq -r 'if .window then (.window.start + ".." + .window.end) else "" end' "$LAST_RUN_MARKER" 2>/dev/null || true)
  threshold=$((REPEAT_NOTICE_HOURS * 3600))

  if [ -n "$last_epoch" ] && printf '%s' "$last_epoch" | grep -Eq '^[0-9]+$'; then
    elapsed=$((now_epoch - last_epoch))
  else
    elapsed=$threshold
  fi

  if [ "$last_date" = "$today" ] || [ "$elapsed" -lt "$threshold" ]; then
    write_repeat_run_notice_text "$last_run_at" "$last_window"
    cat "$REPEAT_RUN_NOTICE_FILE" >&2
    printf '\n' >&2
    REPEAT_RUN_NOTICE_SHOWN=true
  fi
}

write_last_run_marker() {
  local now_iso now_epoch today
  now_iso=$(iso_now)
  now_epoch=$(date '+%s')
  today=$(date '+%Y-%m-%d')
  jq -n \
    --arg last_run_at "$now_iso" \
    --arg run_date "$today" \
    --argjson last_run_epoch "$now_epoch" \
    --arg start "$START" \
    --arg end "$END" \
    --arg kb "$KBDIR" \
    '{
      last_run_at:$last_run_at,
      last_run_epoch:$last_run_epoch,
      run_date:$run_date,
      window:{start:$start,end:$end},
      kb_dir:$kb
    }' > "$LAST_RUN_MARKER" 2>/dev/null || warn "无法写入最近运行标记:$LAST_RUN_MARKER"
}

if ! command -v jq >/dev/null 2>&1; then
  echo "错误:未找到 jq" >&2
  exit 1
fi

collect_context_terms() {
  if [ -z "$KBDIR" ] || [ ! -d "$KBDIR" ]; then
    warn "未找到知识库目录,跳过 context.md / INDEX.md 词表抽取"
    return
  fi

  if [ -f "$KBDIR/context.md" ]; then
    sed 's/[`*_#>|]/ /g;s/[，,。；;：:、\/()（）\[\]【】{}<>]/\
/g' "$KBDIR/context.md" \
      | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' \
      | awk 'length($0) >= 2 && length($0) <= 40 {print}' \
      | grep -Ev '^(暂无|无|none|TODO|TBD|今日|本周|最近|相关|重点|上下文|context|Context)$' \
      >> "$TERMS_LINES" || true
  fi

  if [ -f "$KBDIR/INDEX.md" ]; then
    awk -F'|' '
      /^\|/ {
        for (i=1; i<=NF; i++) {
          gsub(/^[ \t]+|[ \t]+$/, "", $i)
        }
        for (i=1; i<=NF; i++) {
          if ($i ~ /^(project|person|org|event|decision|topic|reading)-/) {
            print $i
            if ((i + 1) <= NF && $(i + 1) !~ /^(标题|title|Title)$/ && length($(i + 1)) >= 2) {
              print $(i + 1)
            }
          }
        }
      }
    ' "$KBDIR/INDEX.md" >> "$TERMS_LINES" || true

    grep -hoE '(oc|ou)_[A-Za-z0-9_-]+' "$KBDIR/INDEX.md" >> "$TERMS_LINES" || true
  fi

  if [ -f "$KBDIR/context.md" ]; then
    grep -hoE '(oc|ou)_[A-Za-z0-9_-]+' "$KBDIR/context.md" >> "$TERMS_LINES" || true
  fi
}

write_static_arrays() {
  {
    printf '%s\n' "todo" "待办" "结论" "决策" "风险" "阻塞" "上线" "回滚" "资源" "排期" "owner" "ddl" "deadline" "测评" "指标" "事故" "方案" "评审" "复盘" "变更" "依赖" "对齐"
    if [ "${#EXTRA_KEYWORDS[@]}" -gt 0 ]; then
      for kw in "${EXTRA_KEYWORDS[@]}"; do printf '%s\n' "$kw"; done
    fi
  } | json_array_from_lines > "$KEYWORDS_JSON"

  printf '%s\n' "待办" "todo" "owner" "ddl" "deadline" "下周" "今天" "明天" "本周" "推进" "需要" "麻烦" "请" "follow" "next step" "排期" "对齐" | json_array_from_lines > "$ACTION_JSON"
  printf '%s\n' "风险" "阻塞" "事故" "回滚" "告警" "异常" "不可用" "稳定性" "重保" "资源不足" "卡住" "延期" "降级" "兜底" "失败" | json_array_from_lines > "$RISK_JSON"
  printf '%s\n' "http://" "https://" "%" "数据" "指标" "截图" "文档" "纪要" "实验" "case" "样本" "链接" | json_array_from_lines > "$EVIDENCE_JSON"
  printf '%s\n' "收到" "好的" "好" "ok" "OK" "嗯" "是" "对" "谢谢" "辛苦" "赞" "mark" "先这样" | json_array_from_lines > "$NOISE_JSON"
}

write_forced_chats() {
  if [ "${#FORCED_CHAT_IDS[@]}" -gt 0 ]; then
    for chat_id in "${FORCED_CHAT_IDS[@]}"; do
      jq -cn --arg chat_id "$chat_id" '{chat_id:$chat_id, chat_name:$chat_id, chat_type:"unknown", source:"forced"}' >> "$CHAT_CANDIDATES"
    done
  fi
}

write_routine_chats_from_kb() {
  [ -n "$KBDIR" ] && [ -d "$KBDIR" ] || return
  grep -rhoE 'oc_[A-Za-z0-9_-]+' "$KBDIR/context.md" "$KBDIR/INDEX.md" 2>/dev/null \
    | awk '!seen[$0]++' \
    | while IFS= read -r chat_id; do
        [ -n "$chat_id" ] || continue
        jq -cn --arg chat_id "$chat_id" '{chat_id:$chat_id, chat_name:$chat_id, chat_type:"unknown", source:"routine-chat"}' >> "$CHAT_CANDIDATES"
      done
}

fetch_chat_list() {
  [ -z "$NO_CHAT_LIST" ] || return
  local token=""
  local got=0
  local page=0
  local tmp="$TMPDIR/chat-list.json"
  local err="$TMPDIR/chat-list.err"
  while [ "$got" -lt "$MAX_CHATS" ]; do
    page=$((page + 1))
    if [ -z "$token" ]; then
      lark-cli im +chat-list --as user --sort-type ByActiveTimeDesc --exclude-muted \
        --page-size "$PAGE_SIZE" --format json >"$tmp" 2>"$err" || true
    else
      lark-cli im +chat-list --as user --sort-type ByActiveTimeDesc --exclude-muted \
        --page-size "$PAGE_SIZE" --page-token "$token" --format json >"$tmp" 2>"$err" || true
    fi
    if ! jq -e '.ok == true' "$tmp" >/dev/null 2>&1; then
      warn "chat-list 失败,降级为显式/定期群与搜索召回:$(head -c 240 "$tmp" "$err" 2>/dev/null | tr '\n' ' ')"
      return
    fi
    jq -c '
      (.data.chats // [])[]?
      | {
          chat_id:(.chat_id // .id // ""),
          chat_name:(.name // .chat_name // .chat_id // .id // ""),
          chat_type:(.chat_type // .type // "unknown"),
          source:"chat-list"
        }
      | select(.chat_id != "")
    ' "$tmp" >> "$CHAT_CANDIDATES"
    got=$(jq -s 'map(select(.source == "chat-list")) | length' "$CHAT_CANDIDATES" 2>/dev/null || echo 0)
    token=$(jq -r '.data.page_token // .data.next_page_token // ""' "$tmp")
    [ "$(jq -r '.data.has_more // false' "$tmp")" = "true" ] || break
    [ -n "$token" ] || break
    [ "$page" -ge 10 ] && { warn "chat-list 达到 10 页保护上限"; break; }
  done
}

parse_search_hits_to_chats() {
  local source="$1"
  local file="$2"
  jq -c --arg source "$source" '
    def hits: (.data.messages // .data.items // .data.results // .data.message_hits // []);
    hits[]?
    | {
        chat_id:(.chat_id // .chat.chat_id // .chat.id // .message.chat_id // ""),
        chat_name:(.chat_name // .chat.name // .message.chat_name // ""),
        chat_type:(.chat_type // .chat.chat_type // "unknown"),
        source:$source
      }
    | select(.chat_id != "")
  ' "$file" >> "$CHAT_CANDIDATES" 2>/dev/null || true
}

run_search_at_me() {
  local tmp="$TMPDIR/search-at-me.json"
  local err="$TMPDIR/search-at-me.err"
  lark-cli im +messages-search --as user --start "$START" --end "$END" --is-at-me \
    --page-all --page-limit "$SEARCH_PAGE_LIMIT" --page-size "$PAGE_SIZE" --format json >"$tmp" 2>"$err" || true
  if jq -e '.ok == true' "$tmp" >/dev/null 2>&1; then
    parse_search_hits_to_chats "search-at-me" "$tmp"
  else
    warn "@我消息搜索失败,跳过该召回:$(head -c 240 "$tmp" "$err" 2>/dev/null | tr '\n' ' ')"
  fi
}

run_search_query() {
  local query="$1"
  local source="$2"
  local safe
  safe=$(printf '%s' "$query" | tr -c 'A-Za-z0-9_-' '_' | cut -c1-40)
  [ -n "$safe" ] || safe="query"
  local tmp="$TMPDIR/search-${safe}.json"
  local err="$TMPDIR/search-${safe}.err"
  lark-cli im +messages-search --as user --start "$START" --end "$END" --query "$query" \
    --page-all --page-limit "$SEARCH_PAGE_LIMIT" --page-size "$PAGE_SIZE" --format json >"$tmp" 2>"$err" || true
  if jq -e '.ok == true' "$tmp" >/dev/null 2>&1; then
    parse_search_hits_to_chats "$source:$query" "$tmp"
  else
    warn "关键词搜索失败($query),跳过:$(head -c 200 "$tmp" "$err" 2>/dev/null | tr '\n' ' ')"
  fi
}

run_queryless_search() {
  [ -n "$QUERYLESS_SEARCH" ] || return
  local tmp="$TMPDIR/search-queryless.json"
  local err="$TMPDIR/search-queryless.err"
  lark-cli im +messages-search --as user --start "$START" --end "$END" \
    --page-all --page-limit "$SEARCH_PAGE_LIMIT" --page-size "$PAGE_SIZE" --format json >"$tmp" 2>"$err" || true
  if jq -e '.ok == true' "$tmp" >/dev/null 2>&1; then
    parse_search_hits_to_chats "search-queryless" "$tmp"
  else
    warn "无关键词 messages-search 不可用,已降级:$(head -c 240 "$tmp" "$err" 2>/dev/null | tr '\n' ' ')"
  fi
}

run_searches() {
  [ -z "$NO_SEARCH" ] || return
  run_search_at_me
  run_queryless_search

  jq -r ".[:$SEARCH_KEYWORDS_LIMIT][]" "$KEYWORDS_JSON" 2>/dev/null \
    | while IFS= read -r kw; do
        [ -n "$kw" ] || continue
        run_search_query "$kw" "search-keyword"
      done

  [ -z "$NO_CONTEXT_SEARCH" ] || return
  jq -r ".[:$CONTEXT_SEARCH_LIMIT][]" "$TERMS_JSON" 2>/dev/null \
    | while IFS= read -r term; do
        [ -n "$term" ] || continue
        printf '%s' "$term" | grep -Eq '^(project|person|org|event|decision|topic|reading)-' && continue
        printf '%s' "$term" | grep -Eq '^(oc|ou)_' && continue
        run_search_query "$term" "search-context"
      done
}

dedupe_candidate_chats() {
  jq -s --argjson max "$MAX_CHATS" '
    map(select(.chat_id != ""))
    | sort_by(.chat_id)
    | group_by(.chat_id)
    | map({
        chat_id:.[0].chat_id,
        chat_name:(([.[].chat_name | select(. != "")][0]) // .[0].chat_id),
        chat_type:(([.[].chat_type | select(. != "" and . != "unknown")][0]) // "unknown"),
        sources:([.[].source] | unique)
      })
    | sort_by((if (.sources | index("forced")) then 0 elif (.sources | index("routine-chat")) then 1 elif (.sources | index("search-at-me")) then 2 else 3 end), .chat_name)
    | .[:$max]
  ' "$CHAT_CANDIDATES" > "$CHAT_JSON"
}

pull_messages_for_chat() {
  local chat="$1"
  local chat_id chat_name chat_sources token page pulled tmp err n has_more
  chat_id=$(printf '%s' "$chat" | jq -r '.chat_id')
  chat_name=$(printf '%s' "$chat" | jq -r '.chat_name')
  chat_sources=$(printf '%s' "$chat" | jq -c '.sources')
  token=""
  page=0
  pulled=0
  tmp="$TMPDIR/messages-${chat_id}.json"
  err="$TMPDIR/messages-${chat_id}.err"

  while [ "$pulled" -lt "$PER_CHAT_LIMIT" ]; do
    [ "$(wc -l < "$MESSAGES_JSONL" | tr -d ' ')" -lt "$GLOBAL_MESSAGE_LIMIT" ] || { warn "达到全局消息上限 $GLOBAL_MESSAGE_LIMIT,停止继续拉取"; return 9; }
    page=$((page + 1))
    if [ -z "$token" ]; then
      lark-cli im +chat-messages-list --as user --chat-id "$chat_id" --start "$START" --end "$END" \
        --sort asc --page-size "$PAGE_SIZE" --format json >"$tmp" 2>"$err" || true
    else
      lark-cli im +chat-messages-list --as user --chat-id "$chat_id" --start "$START" --end "$END" \
        --sort asc --page-size "$PAGE_SIZE" --page-token "$token" --format json >"$tmp" 2>"$err" || true
    fi
    if ! jq -e '.ok == true' "$tmp" >/dev/null 2>&1; then
      warn "拉取会话失败($chat_id $chat_name),跳过:$(head -c 240 "$tmp" "$err" 2>/dev/null | tr '\n' ' ')"
      return 0
    fi

    n=$(jq '(.data.messages // .data.items // []) | length' "$tmp")
    if [ "$n" -eq 0 ]; then
      break
    fi

    jq -c --arg chat_id "$chat_id" --arg chat_name "$chat_name" --argjson chat_sources "$chat_sources" '
      def sender_open_id: (.sender.id // .sender.open_id // .sender.sender_id.open_id // .sender.sender_id.user_id // "");
      def sender_name: (.sender.name // .sender.sender_name // .sender.id // "系统");
      def raw_content: (.content // .body.content // .message.content // "");
      def textify:
        raw_content as $c
        | (if ($c | type) == "string" then (try ($c | fromjson) catch $c) else $c end)
        | if type == "string" then .
          elif type == "object" or type == "array" then ([.. | strings] | join(" "))
          else tostring end
        | gsub("\\s+";" ")
        | gsub("^\\s+|\\s+$";"");
      def normal_time:
        (.create_time // .update_time // .timestamp // "") as $t
        | ($t | tostring) as $s
        | if ($s | test("^[0-9]{13}$")) then (($s | tonumber / 1000) | strftime("%Y-%m-%dT%H:%M:%S+00:00"))
          elif ($s | test("^[0-9]{10}$")) then (($s | tonumber) | strftime("%Y-%m-%dT%H:%M:%S+00:00"))
          else $s end;
      (.data.messages // .data.items // [])[]?
      | {
          chat_id:$chat_id,
          chat_name:$chat_name,
          chat_type:(.chat_type // "unknown"),
          chat_sources:$chat_sources,
          message_id:(.message_id // .id // .message_id_str // ""),
          time:normal_time,
          sender_name:sender_name,
          sender_open_id:sender_open_id,
          msg_type:(.msg_type // .message_type // .type // "unknown"),
          text:textify,
          is_at_me:(.is_at_me // false),
          has_attachment:((.msg_type // .message_type // "") as $t | ($t == "image" or $t == "file" or $t == "media" or $t == "video" or $t == "audio"))
        }
    ' "$tmp" >> "$MESSAGES_JSONL"

    pulled=$((pulled + n))
    has_more=$(jq -r '.data.has_more // false' "$tmp")
    token=$(jq -r '.data.page_token // .data.next_page_token // ""' "$tmp")
    if [ "$pulled" -ge "$PER_CHAT_LIMIT" ] && [ "$has_more" = "true" ]; then
      printf '%s\n' "$chat_id" >> "$TRUNCATED_CHATS"
      warn "会话达到单会话消息上限($chat_id $chat_name):$PER_CHAT_LIMIT"
      break
    fi
    [ "$has_more" = "true" ] || break
    [ -n "$token" ] || break
    [ "$page" -ge 20 ] && { printf '%s\n' "$chat_id" >> "$TRUNCATED_CHATS"; warn "会话达到 20 页保护上限($chat_id $chat_name)"; break; }
  done
  return 0
}

pull_all_messages() {
  local chat rc
  jq -c '.[]' "$CHAT_JSON" | while IFS= read -r chat; do
    [ -n "$chat" ] || continue
    pull_messages_for_chat "$chat"
    rc=$?
    [ "$rc" -eq 9 ] && break
  done
}

build_result_json() {
  local warnings_json truncated_json lark_version first_run_notice_text repeat_run_notice_text
  warnings_json=$(awk 'NF {print}' "$WARN_FILE" | json_array_from_lines)
  truncated_json=$(awk 'NF {print}' "$TRUNCATED_CHATS" | awk '!seen[$0]++' | json_array_from_lines)
  lark_version=$(lark-cli --version 2>/dev/null | head -n1 || true)
  first_run_notice_text=$(cat "$FIRST_RUN_NOTICE_FILE" 2>/dev/null || true)
  repeat_run_notice_text=$(cat "$REPEAT_RUN_NOTICE_FILE" 2>/dev/null || true)

  jq -s \
    --arg start "$START" \
    --arg end "$END" \
    --arg kb "$KBDIR" \
    --arg lark_version "$lark_version" \
    --arg first_run_notice_shown "$FIRST_RUN_NOTICE_SHOWN" \
    --arg first_run_marker "$FIRST_RUN_MARKER" \
    --arg first_run_notice_text "$first_run_notice_text" \
    --arg repeat_run_notice_shown "$REPEAT_RUN_NOTICE_SHOWN" \
    --arg last_run_marker "$LAST_RUN_MARKER" \
    --arg repeat_run_notice_text "$repeat_run_notice_text" \
    --argjson repeat_notice_hours "$REPEAT_NOTICE_HOURS" \
    --argjson candidate_chats "$(cat "$CHAT_JSON")" \
    --argjson terms "$(cat "$TERMS_JSON")" \
    --argjson keywords "$(cat "$KEYWORDS_JSON")" \
    --argjson action_terms "$(cat "$ACTION_JSON")" \
    --argjson risk_terms "$(cat "$RISK_JSON")" \
    --argjson evidence_terms "$(cat "$EVIDENCE_JSON")" \
    --argjson noise_terms "$(cat "$NOISE_JSON")" \
    --argjson warnings "$warnings_json" \
    --argjson truncated_chats "$truncated_json" \
    --argjson max_candidate_threads "$MAX_CANDIDATE_THREADS" \
    --argjson max_llm_threads "$MAX_LLM_THREADS" \
    --argjson representative_limit "$REPRESENTATIVE_LIMIT" \
    --argjson gap "$THREAD_GAP_MINUTES" \
    --argjson min_score "$MIN_SCORE" \
    --argjson max_chats "$MAX_CHATS" \
    --argjson per_chat_limit "$PER_CHAT_LIMIT" \
    --argjson global_message_limit "$GLOBAL_MESSAGE_LIMIT" \
    --argjson page_size "$PAGE_SIZE" '
      def clean_text: (.text // "" | tostring | gsub("\\s+";" ") | gsub("^\\s+|\\s+$";""));
      def matched($txt; $arr; $prefix): [
        $arr[] as $term
        | select((($term | length) > 1) and (($txt | ascii_downcase) | contains($term | ascii_downcase)))
        | "\($prefix):\($term)"
      ];
      def source_priority:
        (if (.chat_sources // [] | index("forced")) then 8 else 0 end)
        + (if (.chat_sources // [] | index("routine-chat")) then 5 else 0 end)
        + (if ([.chat_sources[]? | select(startswith("search-at-me"))] | length) > 0 then 6 else 0 end)
        + (if ([.chat_sources[]? | select(startswith("search-context"))] | length) > 0 then 3 else 0 end);
      def noise_penalty($noise_matches):
        (if (clean_text | length) <= 2 then 8 else 0 end)
        + (if (clean_text | ascii_downcase | test("^(ok|收到|好的|好|嗯|是|对|谢谢|辛苦|赞|mark)$")) then 6 else 0 end)
        + (if ($noise_matches | length) > 0 and (clean_text | length) <= 12 then 4 else 0 end)
        + (if (.msg_type == "system" or .msg_type == "reaction") then 10 else 0 end)
        + (if (.has_attachment == true and (clean_text | length) == 0 and .is_at_me != true) then 5 else 0 end);
      def minute_bucket:
        (.time | tostring) as $t
        | if ($t | test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}")) then
            ($t[14:16] | tonumber) as $m
            | (($m / $gap | floor) * $gap) as $bm
            | ($t[0:14] + (if $bm < 10 then "0" + ($bm | tostring) else ($bm | tostring) end))
          else "unknown" end;

      . as $messages
      | ($messages
          | map(
              . as $m
              | (clean_text) as $txt
              | (matched($txt; $keywords; "keyword")) as $kw
              | (matched($txt; $action_terms; "action")) as $actions
              | (matched($txt; $risk_terms; "risk")) as $risks
              | (matched($txt; $evidence_terms; "evidence")) as $evidence
              | (matched($txt; $noise_terms; "noise")) as $noise
              | (matched($txt; $terms; "context")) as $ctx
              | (if ($kw | length) > 6 then 6 else ($kw | length) end) as $kw_count
              | (if ($ctx | length) > 5 then 5 else ($ctx | length) end) as $ctx_count
              | (if ($actions | length) > 3 then 3 else ($actions | length) end) as $action_count
              | (if ($risks | length) > 3 then 3 else ($risks | length) end) as $risk_count
              | (if ($evidence | length) > 2 then 2 else ($evidence | length) end) as $evidence_count
              | (if ((.is_at_me == true) or (clean_text | test("<at|@我"))) then 8 else 0 end) as $mention_score
              | (
                  ($kw_count * 2)
                  + ($ctx_count * 3)
                  + ($action_count * 4)
                  + ($risk_count * 5)
                  + ($evidence_count * 3)
                  + $mention_score
                  + source_priority
                  - noise_penalty($noise)
                ) as $score
              | . + {
                  local_score:$score,
                  score_reasons:(
                    ($kw + $ctx + $actions + $risks + $evidence)
                    + (if $mention_score > 0 then ["at_me"] else [] end)
                    + (if source_priority > 0 then ["source_priority"] else [] end)
                    + (if noise_penalty($noise) > 0 then ["noise_penalty:-" + (noise_penalty($noise) | tostring)] else [] end)
                  ),
                  matched_terms:($ctx | map(sub("^context:";""))),
                  time_bucket:minute_bucket,
                  chat_truncated:(($truncated_chats | index($m.chat_id)) != null)
                }
            )
        ) as $scored
      | ($scored
          | map(select(.local_score >= $min_score))
          | sort_by(.chat_id, .time_bucket, .time)
        ) as $candidate_messages
      | ($candidate_messages
          | group_by(.chat_id + "|" + .time_bucket)
          | map(
              . as $msgs
              | {
                  thread_id:(($msgs[0].chat_id // "chat") + "|" + ($msgs[0].time_bucket // "unknown")),
                  chat_id:$msgs[0].chat_id,
                  chat_name:$msgs[0].chat_name,
                  chat_type:$msgs[0].chat_type,
                  chat_sources:$msgs[0].chat_sources,
                  start_time:([ $msgs[].time ] | min),
                  end_time:([ $msgs[].time ] | max),
                  time_bucket:$msgs[0].time_bucket,
                  candidate_message_count:($msgs | length),
                  local_score:([ $msgs[].local_score ] | add // 0),
                  max_message_score:([ $msgs[].local_score ] | max // 0),
                  score_reasons:([ $msgs[].score_reasons[]? ] | unique | .[:30]),
                  matched_terms:([ $msgs[].matched_terms[]? ] | unique | .[:20]),
                  participants:([ $msgs[] | {name:.sender_name, open_id:.sender_open_id} ] | unique_by(.open_id, .name) | .[:20]),
                  message_ids:([ $msgs[].message_id | select(. != "") ] | unique),
                  representative_messages:($msgs | sort_by(.local_score) | reverse | .[:$representative_limit] | map({
                    message_id,
                    time,
                    sender_name,
                    sender_open_id,
                    msg_type,
                    text,
                    local_score,
                    score_reasons
                  })),
                  thread_truncated:([ $msgs[].chat_truncated ] | any)
                }
            )
          | sort_by(.local_score)
          | reverse
        ) as $all_threads
      | {
          ok:true,
          window:{start:$start, end:$end},
          kb_dir:$kb,
          lark_cli_version:$lark_version,
          first_run_notice:{
            shown:($first_run_notice_shown == "true"),
            marker_path:$first_run_marker,
            text:$first_run_notice_text
          },
          repeat_run_notice:{
            shown:($repeat_run_notice_shown == "true"),
            marker_path:$last_run_marker,
            repeat_notice_hours:$repeat_notice_hours,
            recommended_frequency:"once_per_day",
            text:$repeat_run_notice_text
          },
          budgets:{
            max_chats:$max_chats,
            per_chat_limit:$per_chat_limit,
            global_message_limit:$global_message_limit,
            page_size:$page_size,
            max_candidate_threads:$max_candidate_threads,
            max_llm_threads:$max_llm_threads,
            thread_gap_minutes:$gap,
            representative_limit:$representative_limit,
            min_score:$min_score
          },
          stats:{
            candidate_chats:($candidate_chats | length),
            raw_messages:($messages | length),
            candidate_messages:($candidate_messages | length),
            candidate_threads:($all_threads | length),
            emitted_threads:($all_threads[:$max_llm_threads] | length),
            truncated_chats:($truncated_chats | length),
            context_terms:($terms | length)
          },
          context_terms_sample:($terms[:80]),
          keywords:$keywords,
          candidate_chats:$candidate_chats,
          threads:($all_threads[:$max_candidate_threads] | .[:$max_llm_threads]),
          warnings:$warnings
        }
    ' "$MESSAGES_JSONL" > "$RESULT_JSON"
}

if [ -z "$DRY_RUN" ]; then
  if ! command -v lark-cli >/dev/null 2>&1; then
    echo "错误:未找到 lark-cli;请先确认 PATH 中可执行 lark-cli,或按 lark-shared 重新安装/登录" >&2
    exit 1
  fi
  show_first_run_notice_if_needed
  show_repeat_run_notice_if_needed
fi

collect_context_terms
awk 'NF {print}' "$TERMS_LINES" \
  | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' \
  | awk 'length($0) >= 2 && length($0) <= 80' \
  | awk '!seen[$0]++' \
  | head -n 400 \
  | json_array_from_lines > "$TERMS_JSON"
write_static_arrays

if [ -n "$DRY_RUN" ]; then
  write_first_run_notice_text
  jq -n \
    --arg start "$START" \
    --arg end "$END" \
    --arg kb "$KBDIR" \
    --arg first_run_marker "$FIRST_RUN_MARKER" \
    --arg first_run_notice_text "$(cat "$FIRST_RUN_NOTICE_FILE")" \
    --arg last_run_marker "$LAST_RUN_MARKER" \
    --argjson repeat_notice_hours "$REPEAT_NOTICE_HOURS" \
    --argjson terms "$(cat "$TERMS_JSON")" \
    --argjson keywords "$(cat "$KEYWORDS_JSON")" \
    --argjson max_chats "$MAX_CHATS" \
    --argjson per_chat_limit "$PER_CHAT_LIMIT" \
    --argjson global_message_limit "$GLOBAL_MESSAGE_LIMIT" \
    '{
      ok:true,
      dry_run:true,
      window:{start:$start,end:$end},
      kb_dir:$kb,
      first_run_notice:{shown:false, marker_path:$first_run_marker, text:$first_run_notice_text},
      repeat_run_notice:{shown:false, marker_path:$last_run_marker, repeat_notice_hours:$repeat_notice_hours, recommended_frequency:"once_per_day", text:""},
      budgets:{max_chats:$max_chats,per_chat_limit:$per_chat_limit,global_message_limit:$global_message_limit},
      context_terms_sample:$terms[:80],
      keywords:$keywords
    }'
  exit 0
fi

write_forced_chats
write_routine_chats_from_kb
fetch_chat_list
run_searches
dedupe_candidate_chats

if [ "$(jq 'length' "$CHAT_JSON")" -eq 0 ]; then
  warn "没有发现可扫描会话"
  printf '[]\n' > "$CHAT_JSON"
else
  pull_all_messages
fi

if [ ! -s "$MESSAGES_JSONL" ]; then
  : > "$MESSAGES_JSONL"
fi

build_result_json || { echo "错误:生成结果 JSON 失败" >&2; exit 1; }
write_last_run_marker

if [ -n "$OUT" ]; then
  cp "$RESULT_JSON" "$OUT"
  printf 'output=%s\n' "$OUT"
else
  cat "$RESULT_JSON"
fi
