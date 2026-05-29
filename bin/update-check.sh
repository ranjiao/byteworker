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
#
# 协议适配:本仓库是 public repo,HTTPS 拉取无需认证。
# 若当前 origin 是 SSH(git@github.com) 但环境无 SSH key,
# 会 fallback 到 HTTPS 临时拉取;默认不改写 origin。
# 如确需脚本补/改 remote,设置 BYTEWORKER_AUTO_UPDATE_MUTATE_ORIGIN=1。
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

BR=$(git -C "$DIR" symbolic-ref --short HEAD 2>/dev/null) || \
   BR=$(git -C "$DIR" rev-parse --abbrev-ref HEAD 2>/dev/null) || \
   BR="master"
BEFORE=$(git -C "$DIR" rev-parse HEAD 2>/dev/null) || exit 0

# ── fetch 阶段:尝试连通 GitHub ──
FETCH_OK=0
USED_FETCH_HEAD=0

# 1) 先尝试用当前 origin fetch
if git -C "$DIR" remote get-url origin >/dev/null 2>&1; then
  if git -C "$DIR" fetch --quiet origin "$BR" 2>/dev/null; then
    FETCH_OK=1
  fi
fi

# 2) 如果失败,尝试用 HTTPS URL 直接 fetch(public repo 无需认证)
#    这是给「origin 是 SSH 但无 SSH key」或「origin 缺失」的用户兜底
if [ "$FETCH_OK" -eq 0 ]; then
  if git -C "$DIR" fetch --quiet "$REPO_URL" "$BR" 2>/dev/null; then
    FETCH_OK=1
    USED_FETCH_HEAD=1
    # 默认不改写 shared/dev checkout 的 origin;需要时显式打开。
    if [ "${BYTEWORKER_AUTO_UPDATE_MUTATE_ORIGIN:-0}" = "1" ]; then
      if git -C "$DIR" remote get-url origin >/dev/null 2>&1; then
        git -C "$DIR" remote set-url origin "$REPO_URL" 2>/dev/null || true
      else
        git -C "$DIR" remote add origin "$REPO_URL" 2>/dev/null || true
        echo "byteworker:已自动补上缺失的 git remote(origin),自动更新恢复。"
      fi
    fi
  fi
fi

# 3) 都失败了 → 网络/代理问题,给用户一句提示(不再完全静默)
if [ "$FETCH_OK" -eq 0 ]; then
  echo "byteworker:无法连接 GitHub,自动更新跳过(检查网络或代理设置)。"
  exit 0
fi

# ── merge 阶段:fast-forward 安全更新 ──
REMOTE_REF="origin/$BR"

# 检查远程分支是否存在
if [ "$USED_FETCH_HEAD" -eq 1 ] || ! git -C "$DIR" rev-parse --verify "$REMOTE_REF" >/dev/null 2>&1; then
  # 如果上面用的是直接 URL fetch,origin/$BR 可能不存在,用 FETCH_HEAD
  REMOTE_REF="FETCH_HEAD"
fi

# 检查是否能 fast-forward(本地有改动/分叉时拒绝覆盖)
if ! git -C "$DIR" merge-base --is-ancestor HEAD "$REMOTE_REF" 2>/dev/null; then
  echo "byteworker:本地有改动或版本已分叉,无法自动 fast-forward 更新。如需手动处理,到 $DIR 执行 git status 查看。"
  exit 0
fi

# 执行 ff merge
if ! git -C "$DIR" merge --ff-only "$REMOTE_REF" --quiet 2>/dev/null; then
  echo "byteworker:自动更新合并失败,跳过。"
  exit 0
fi

AFTER=$(git -C "$DIR" rev-parse HEAD 2>/dev/null) || exit 0

if [ "$BEFORE" != "$AFTER" ]; then
  N=$(git -C "$DIR" rev-list --count "${BEFORE}..${AFTER}" 2>/dev/null || echo "若干")
  echo "byteworker skill 已自动更新(拉取 $N 个提交,更新于下次使用生效)。"
fi
exit 0
