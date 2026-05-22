#!/usr/bin/env bash
# byteworker · update-check.sh
# 静默自动更新:每周最多一次,从 GitHub fast-forward 拉取最新 skill 内容。
# 由 SKILL.md「操作前必读」在每次使用 skill 时最先调用。
#
# 用法:
#   bin/update-check.sh           # 每周一次,到期才真检查
#   bin/update-check.sh --force   # 忽略周期,立即检查
#
# 输出约定:
#   有输出 = 已更新,或自动更新不可用、需用户处理(SKILL.md 把该行转告用户);
#   无输出 = 未到检查周期 / 已是最新 / 检查被安全跳过(离线等)。
# 始终 exit 0,绝不打断调用方。
set -uo pipefail

REPO_URL="https://github.com/ranjiao/byteworker.git"

FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

DIR=$(cd "$(dirname "$0")/.." 2>/dev/null && pwd) || exit 0
STAMP="$DIR/.last-update-check"
INTERVAL=$((7 * 24 * 3600))   # 一周
NOW=$(date +%s)

# 周期节流:7 天内检查过则静默退出(--force 跳过)
if [ "$FORCE" -ne 1 ] && [ -f "$STAMP" ]; then
  LAST=$(tr -cd '0-9' < "$STAMP" 2>/dev/null)
  [ -n "$LAST" ] && [ $((NOW - LAST)) -lt "$INTERVAL" ] && exit 0
fi
# 记录本次检查时间(无论后续成败,保证一周才再试一次)
echo "$NOW" > "$STAMP" 2>/dev/null || true

# skill 目录必须是 git 仓库 —— 否则自动更新无从做起。
# 常见于用 zip 下载、或「git init + 手工拼文件」安装的环境:不再静默,提示用户重装。
if ! git -C "$DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "byteworker:skill 目录不是 git 仓库,自动更新已停用 —— 按 INSTALL.md 用 \`git clone\` 重装即可恢复。"
  exit 0
fi

# 自愈:缺 origin remote 则补上(skill 仓库本就应跟踪该 GitHub 公共仓库)。
# 注意:本脚本只动 skill 仓库($DIR),绝不触碰知识库数据目录的 git。
if ! git -C "$DIR" remote get-url origin >/dev/null 2>&1; then
  git -C "$DIR" remote add origin "$REPO_URL" 2>/dev/null \
    && echo "byteworker:已自动补上缺失的 git remote(origin),自动更新恢复。"
fi
git -C "$DIR" remote get-url origin >/dev/null 2>&1 || exit 0

BR=$(git -C "$DIR" symbolic-ref --short HEAD 2>/dev/null) || exit 0
BEFORE=$(git -C "$DIR" rev-parse HEAD 2>/dev/null) || exit 0
# fast-forward-only:本地有改动/已分叉时会安全失败,不会冲突、不会覆盖。
# 显式带 origin + 分支名 —— 即便 remote 是刚补上的、没有 upstream 跟踪也能拉。
git -C "$DIR" pull --ff-only --quiet origin "$BR" 2>/dev/null || exit 0
AFTER=$(git -C "$DIR" rev-parse HEAD 2>/dev/null) || exit 0

if [ "$BEFORE" != "$AFTER" ]; then
  N=$(git -C "$DIR" rev-list --count "${BEFORE}..${AFTER}" 2>/dev/null || echo "若干")
  echo "byteworker skill 已自动更新(拉取 $N 个提交,更新于下次使用生效)。"
fi
exit 0
