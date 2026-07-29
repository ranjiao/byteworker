#!/usr/bin/env bash
# byteworker · check-deps.sh
# 检查 byteworker 运行所需的前置依赖,逐项报告。安装时用于确认环境。
#
# 退出码:
#   0 = 全部就绪
#   1 = Tier 1(byteworker 自身)有缺失 —— 必须先装
#   2 = 仅 Tier 2(飞书生态)有缺失 —— byteworker 可先装,摄取飞书内容前需补齐
set -uo pipefail

T1=0; T2=0

have()          { command -v "$1" >/dev/null 2>&1; }
skill_present() {
  for d in "${CODEX_HOME:-$HOME/.codex}/skills" "$HOME/.claude/skills" "$HOME/.openclaw/skills" "$HOME/.agents/skills"; do
    [ -e "$d/$1" ] && return 0
  done
  return 1
}
mark()          { if [ "$1" = ok ]; then echo "  ✓ $2"; else echo "  ✗ $2 —— 缺失"; fi; }

echo "── Tier 1 · byteworker 自身(必须)──"
for c in git jq bash; do
  if have "$c"; then mark ok "$c"; else mark no "$c"; T1=1; fi
done
if have python3 && python3 -c 'import zoneinfo' >/dev/null 2>&1; then
  mark ok "python3 (>= 3.9)"
else
  mark no "python3 (>= 3.9)"
  T1=1
fi

echo
echo "── Tier 2 · 内部来源(使用对应来源时需要)──"
if have lark-cli; then mark ok "lark-cli"; else mark no "lark-cli(需经 npm/node 安装)"; T2=1; fi
if have meegle; then mark ok "meegle"; else mark no "meegle(摄取 Meego 视图需要)"; T2=1; fi
for s in lark-doc lark-minutes lark-vc lark-im lark-calendar lark-contact lark-base meegle; do
  if skill_present "$s"; then mark ok "skill: $s"; else mark no "skill: $s"; T2=1; fi
done

echo
if [ "$T1" -ne 0 ]; then
  echo "结论:✗ Tier 1 有缺失 —— 必须先装再继续。"
  echo "  macOS: brew install git jq python   |   Linux: sudo apt install git jq python3"
  exit 1
elif [ "$T2" -ne 0 ]; then
  echo "结论:Tier 1 就绪;✗ Tier 2 有缺失 —— byteworker 可先装,但摄取飞书内容前需补齐:"
  echo "  · 安装 lark-cli / meegle；登录与最小授权由安装流程在用户选择后引导"
  echo "  · 安装 lark-* / meegle skills(lark-doc / minutes / vc / im / calendar / contact / base / meegle)"
  exit 2
else
  echo "结论:✓ 依赖齐全。登录状态另用 byteworker-cli.py source auth-status 只读检查。"
  exit 0
fi
