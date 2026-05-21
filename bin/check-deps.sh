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
skill_present() { [ -e "$HOME/.claude/skills/$1" ] || [ -e "$HOME/.agents/skills/$1" ]; }
mark()          { if [ "$1" = ok ]; then echo "  ✓ $2"; else echo "  ✗ $2 —— 缺失"; fi; }

echo "── Tier 1 · byteworker 自身(必须)──"
for c in git jq bash; do
  if have "$c"; then mark ok "$c"; else mark no "$c"; T1=1; fi
done

echo
echo "── Tier 2 · 飞书生态(摄取飞书内容必须)──"
if have lark-cli; then mark ok "lark-cli"; else mark no "lark-cli(需经 npm/node 安装)"; T2=1; fi
for s in lark-doc lark-minutes lark-vc lark-im lark-calendar lark-contact; do
  if skill_present "$s"; then mark ok "skill: $s"; else mark no "skill: $s"; T2=1; fi
done

echo
if [ "$T1" -ne 0 ]; then
  echo "结论:✗ Tier 1 有缺失 —— 必须先装再继续。"
  echo "  macOS: brew install git jq   |   Linux: sudo apt install git jq"
  exit 1
elif [ "$T2" -ne 0 ]; then
  echo "结论:Tier 1 就绪;✗ Tier 2 有缺失 —— byteworker 可先装,但摄取飞书内容前需补齐:"
  echo "  · 安装 lark-cli 并执行 lark-cli auth login 登录飞书"
  echo "  · 安装 lark-* skills(lark-doc / minutes / vc / im / calendar / contact)"
  exit 2
else
  echo "结论:✓ 依赖齐全。提醒:确保已执行过 lark-cli auth login(登录飞书)。"
  exit 0
fi
