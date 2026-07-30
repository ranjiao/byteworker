#!/usr/bin/env bash
# byteworker · check-deps.sh
# 检查 byteworker 运行所需的前置依赖,逐项报告。安装时用于确认环境。
# 运行期请调用 `bin/byteworker preflight`；本脚本复用同一个 runtime resolver。
#
# 退出码:
#   0 = 全部就绪
#   1 = Tier 1(byteworker 自身)有缺失 —— 必须先装
#   2 = 仅 Tier 2(飞书生态)有缺失 —— byteworker 可先装,摄取飞书内容前需补齐
set -uo pipefail

DIR=$(cd "$(dirname "$0")" 2>/dev/null && pwd) || exit 1
exec "$DIR/byteworker" deps
