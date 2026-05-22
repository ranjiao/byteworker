#!/usr/bin/env bash
# byteworker · resolve-users.sh
# 把飞书 open_id 批量反查成姓名,供文档摄取(feishu_doc)解析 @ 提及使用。
#
# 用法:
#   bin/resolve-users.sh --from-doc <file>        # 从文件里 grep 出所有 ou_ 开头的 open_id 再解析
#   bin/resolve-users.sh --ids ou_x,ou_y,...      # 直接给 open_id(CSV)
#   cat ids.txt | bin/resolve-users.sh            # 从 stdin 读,一行一个 open_id
#
# 输出(stdout):每行 "<open_id>\t<姓名>\t<feishu_id>"
#   feishu_id = 企业邮箱 @ 前缀(飞书英文 id,全局唯一);解析不到的字段填 ?。
#   进度/汇总打到 stderr。
# 退出码:0 成功 | 1 参数/环境错误
set -uo pipefail

IDS=""; FROMDOC=""
while [ $# -gt 0 ]; do
  case "$1" in
    --ids)      IDS="${2:-}"; shift 2;;
    --from-doc) FROMDOC="${2:-}"; shift 2;;
    -h|--help)  sed -n '2,12p' "$0"; exit 0;;
    *) echo "未知参数:$1" >&2; exit 1;;
  esac
done

command -v lark-cli >/dev/null 2>&1 || { echo "错误:未找到 lark-cli" >&2; exit 1; }
command -v jq      >/dev/null 2>&1 || { echo "错误:未找到 jq" >&2; exit 1; }

TMP=$(mktemp); CLEAN=$(mktemp)
trap 'rm -f "$TMP" "$CLEAN"' EXIT

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
RESOLVED=0
while read -r OID; do
  [ -z "$OID" ] && continue
  U=$(lark-cli contact +get-user --user-id "$OID" --user-id-type open_id --as user 2>/dev/null)
  NAME=$(printf '%s' "$U" | jq -r '.data.user.name // ""')
  EMAIL=$(printf '%s' "$U" | jq -r '.data.user.enterprise_email // .data.user.email // ""')
  FID="${EMAIL%%@*}"                 # 企业邮箱 @ 前缀 = 飞书英文 id
  [ -z "$NAME" ] && NAME="?"
  [ -z "$FID" ] && FID="?"
  printf '%s\t%s\t%s\n' "$OID" "$NAME" "$FID"
  [ "$NAME" != "?" ] && RESOLVED=$((RESOLVED + 1))
done < "$CLEAN"
echo "resolved=$RESOLVED/$N" >&2
