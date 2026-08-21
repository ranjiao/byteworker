#!/usr/bin/env bash
# byteworker · resolve-users.sh
# 把飞书 open_id 批量反查成姓名,供文档 / 群聊摄取解析人物使用。
#
# 用法:
#   bin/resolve-users.sh --from-doc <file>        # 从文档 raw / 群聊 transcript 里 grep 出所有 ou_ 开头的 open_id 再解析
#   bin/resolve-users.sh --ids ou_x,ou_y,...      # 直接给 open_id(CSV)
#   bin/resolve-users.sh --ids ou_x,ou_y --jobs 4 # 最多 4 路并发，输出仍按 ID 排序
#   cat ids.txt | bin/resolve-users.sh            # 从 stdin 读,一行一个 open_id
#
# 默认输出(stdout):每行 "<open_id>\t<姓名>\t<feishu_id>"，保持旧调用兼容。
#   --format json 输出姓名、邮箱、部门路径和本次通讯录核验时间，供 person 创建/更新使用。
#   feishu_id = 企业邮箱 @ 前缀(飞书英文 id,全局唯一);解析不到的身份字段填 ?。
#   进度/汇总打到 stderr。
# 退出码:0 成功 | 1 参数/环境错误
set -uo pipefail

IDS=""; FROMDOC=""; FORMAT="tsv"; JOBS="${BYTEWORKER_RESOLVE_JOBS:-4}"
while [ $# -gt 0 ]; do
  case "$1" in
    --ids)      IDS="${2:-}"; shift 2;;
    --from-doc) FROMDOC="${2:-}"; shift 2;;
    --format)   FORMAT="${2:-}"; shift 2;;
    --jobs)     JOBS="${2:-}"; shift 2;;
    -h|--help)  sed -n '2,14p' "$0"; exit 0;;
    *) echo "未知参数:$1" >&2; exit 1;;
  esac
done

case "$FORMAT" in
  tsv|json) ;;
  *) echo "错误:--format 仅支持 tsv 或 json" >&2; exit 1;;
esac
case "$JOBS" in
  ''|*[!0-9]*) echo "错误:--jobs 必须为 1-4" >&2; exit 1;;
esac
[ "$JOBS" -ge 1 ] && [ "$JOBS" -le 4 ] || { echo "错误:--jobs 必须为 1-4" >&2; exit 1; }

command -v lark-cli >/dev/null 2>&1 || { echo "错误:未找到 lark-cli" >&2; exit 1; }
command -v jq      >/dev/null 2>&1 || { echo "错误:未找到 jq" >&2; exit 1; }

TMP=$(mktemp); CLEAN=$(mktemp); JSON_ROWS=$(mktemp); WORKDIR=$(mktemp -d)
trap 'rm -f "$TMP" "$CLEAN" "$JSON_ROWS"; rm -rf "$WORKDIR"' EXIT

# 收集 open_id:--from-doc grep / --ids CSV / stdin
if [ -n "$FROMDOC" ]; then
  [ -f "$FROMDOC" ] || { echo "错误:文件不存在:$FROMDOC" >&2; exit 1; }
  grep -oE 'ou_[a-zA-Z0-9]+' "$FROMDOC" >> "$TMP" 2>/dev/null || true
fi
[ -n "$IDS" ] && echo "$IDS" | tr ',' '\n' >> "$TMP"
if [ -z "$FROMDOC" ] && [ -z "$IDS" ] && [ ! -t 0 ]; then cat >> "$TMP"; fi

# 清洗 + 去重
grep -oE 'ou_[a-zA-Z0-9]+' "$TMP" 2>/dev/null | sort -u > "$CLEAN" || true
N=$(wc -l < "$CLEAN" | tr -d ' ')
if [ "$N" -eq 0 ]; then
  echo "错误:未提供任何 open_id(用 --from-doc / --ids / stdin)" >&2; exit 1
fi

echo "解析 $N 个 open_id ..." >&2
RESOLVED_AT=$(date '+%Y-%m-%dT%H:%M:%S%z' | sed -E 's/([+-][0-9]{2})([0-9]{2})$/\1:\2/')
resolve_one() {
  local INDEX="$1" OID="$2" U NAME ENTERPRISE_EMAIL PERSONAL_EMAIL
  local DEPARTMENT_PATH IS_ACTIVATED IS_CROSS_TENANT EMAIL FID ROW
  ROW=$(printf '%s/%06d.row' "$WORKDIR" "$INDEX")
  U=$(lark-cli contact +search-user --user-ids "$OID" --as user 2>/dev/null || true)
  NAME=$(printf '%s' "$U" | jq -r '.data.users[0].localized_name // .data.users[0].name // ""' 2>/dev/null)
  ENTERPRISE_EMAIL=$(printf '%s' "$U" | jq -r '.data.users[0].enterprise_email // ""' 2>/dev/null)
  PERSONAL_EMAIL=$(printf '%s' "$U" | jq -r '.data.users[0].email // ""' 2>/dev/null)
  DEPARTMENT_PATH=$(printf '%s' "$U" | jq -r '.data.users[0].department // ""' 2>/dev/null)
  IS_ACTIVATED=$(printf '%s' "$U" | jq -r '.data.users[0].is_activated // false' 2>/dev/null)
  IS_CROSS_TENANT=$(printf '%s' "$U" | jq -r '.data.users[0].is_cross_tenant // false' 2>/dev/null)
  EMAIL="${ENTERPRISE_EMAIL:-$PERSONAL_EMAIL}"
  if [ -z "$NAME" ] && [ -z "$EMAIL" ]; then
    U=$(lark-cli contact +get-user --user-id "$OID" --user-id-type open_id --as user 2>/dev/null || true)
    NAME=$(printf '%s' "$U" | jq -r '.data.user.name // ""' 2>/dev/null)
    ENTERPRISE_EMAIL=$(printf '%s' "$U" | jq -r '.data.user.enterprise_email // ""' 2>/dev/null)
    PERSONAL_EMAIL=$(printf '%s' "$U" | jq -r '.data.user.email // ""' 2>/dev/null)
    EMAIL="${ENTERPRISE_EMAIL:-$PERSONAL_EMAIL}"
  fi
  case "$IS_ACTIVATED" in true|false) ;; *) IS_ACTIVATED=false;; esac
  case "$IS_CROSS_TENANT" in true|false) ;; *) IS_CROSS_TENANT=false;; esac
  FID="${ENTERPRISE_EMAIL%%@*}"      # 只允许企业邮箱前缀成为飞书英文 id
  [ -z "$NAME" ] && NAME="?"
  [ -z "$FID" ] && FID="?"
  if [ "$FORMAT" = "json" ]; then
    jq -cn \
      --arg open_id "$OID" \
      --arg name "$NAME" \
      --arg feishu_id "$FID" \
      --arg email "$EMAIL" \
      --arg enterprise_email "$ENTERPRISE_EMAIL" \
      --arg department_path "$DEPARTMENT_PATH" \
      --argjson is_activated "$IS_ACTIVATED" \
      --argjson is_cross_tenant "$IS_CROSS_TENANT" \
      '{
        open_id: $open_id,
        name: $name,
        feishu_id: $feishu_id,
        email: $email,
        enterprise_email: $enterprise_email,
        department_path: $department_path,
        is_activated: $is_activated,
        is_cross_tenant: $is_cross_tenant
      }' > "$ROW"
  else
    printf '%s\t%s\t%s\n' "$OID" "$NAME" "$FID" > "$ROW"
  fi
  if [ "$NAME" != "?" ] && [ "$FID" != "?" ]; then
    : > "${ROW%.row}.ok"
  fi
  return 0
}

wait_batch() {
  local PID
  for PID in $PIDS; do
    wait "$PID" || true
  done
  PIDS=""
  ACTIVE=0
}

PIDS=""; ACTIVE=0; INDEX=0
while read -r OID; do
  [ -z "$OID" ] && continue
  INDEX=$((INDEX + 1))
  resolve_one "$INDEX" "$OID" &
  PIDS="$PIDS $!"
  ACTIVE=$((ACTIVE + 1))
  if [ "$ACTIVE" -ge "$JOBS" ]; then
    wait_batch
  fi
done < "$CLEAN"
wait_batch

RESOLVED=0
for ROW in "$WORKDIR"/*.row; do
  [ -e "$ROW" ] || continue
  if [ "$FORMAT" = "json" ]; then
    cat "$ROW" >> "$JSON_ROWS"
  else
    cat "$ROW"
  fi
  [ -f "${ROW%.row}.ok" ] && RESOLVED=$((RESOLVED + 1))
done
if [ "$FORMAT" = "json" ]; then
  jq -s \
    --arg resolved_at "$RESOLVED_AT" \
    '{
      schema_version: "byteworker-resolved-users/v1",
      resolved_at: $resolved_at,
      users: .
    }' "$JSON_ROWS"
fi
echo "resolved=$RESOLVED/$N" >&2
