#!/usr/bin/env bash
# byteworker · update-check.sh
# 静默自动更新:成功检查后每周最多一次,失败按退避周期重试。
# 由 SKILL.md「操作前必读」在每次使用 skill 时最先调用。
#
# 用法:
#   bin/update-check.sh           # 每周一次,到期才真检查
#   bin/update-check.sh --force   # 忽略周期,立即检查
#
# 输出约定:
#   有输出 = 已更新及其 doctor 摘要,或自动更新不可用、需用户处理
#             (SKILL.md 把该行转告用户);
#   无输出 = 未到检查/重试周期、另一进程正在检查、或已是最新。
# 始终 exit 0,绝不打断调用方。
#
# 协议适配:本仓库是 public repo,HTTPS 拉取无需认证。
# 若当前 origin 是 SSH(git@github.com) 但环境无 SSH key,
# 会 fallback 到 HTTPS 临时拉取;默认不改写 origin。
# 如确需脚本补/改 remote,设置 BYTEWORKER_AUTO_UPDATE_MUTATE_ORIGIN=1。
set -uo pipefail

REPO_URL="${BYTEWORKER_UPDATE_REPO_URL:-https://github.com/ranjiao/byteworker.git}"

FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

DIR=$(cd "$(dirname "$0")/.." 2>/dev/null && pwd) || exit 0
STAMP="$DIR/.last-update-check" # legacy migration input; no longer written
STATE="$DIR/.update-state.json"
LOCK_DIR="$DIR/.update-check.lock"
INTERVAL="${BYTEWORKER_UPDATE_INTERVAL_SECONDS:-$((7 * 24 * 3600))}"
RETRY_BASE="${BYTEWORKER_UPDATE_RETRY_BASE_SECONDS:-3600}"
RETRY_MAX="${BYTEWORKER_UPDATE_RETRY_MAX_SECONDS:-21600}"
POSTFLIGHT_RETRY_BASE="${BYTEWORKER_POSTFLIGHT_RETRY_BASE_SECONDS:-300}"
POSTFLIGHT_RETRY_MAX="${BYTEWORKER_POSTFLIGHT_RETRY_MAX_SECONDS:-3600}"
NOW="${BYTEWORKER_UPDATE_NOW:-$(date +%s)}"
NOTICE=""
PYTHON_BIN="${BYTEWORKER_PYTHON_BIN:-python3}"

[ "${BYTEWORKER_NO_AUTO_UPDATE:-0}" = "1" ] && exit 0

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1 || [ ! -f "$DIR/bin/update-state.py" ]; then
  echo "byteworker:更新状态工具不可用,自动更新已停用。"
  exit 0
fi

append_notice() {
  [ -z "${1:-}" ] && return
  if [ -n "$NOTICE" ]; then
    NOTICE="$NOTICE $1"
  else
    NOTICE="$1"
  fi
}

release_lock() {
  rm -f "$LOCK_DIR/pid" 2>/dev/null || true
  rmdir "$LOCK_DIR" 2>/dev/null || true
}

acquire_lock() {
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "$$" > "$LOCK_DIR/pid" 2>/dev/null || true
    return 0
  fi
  OWNER=$(tr -cd '0-9' < "$LOCK_DIR/pid" 2>/dev/null || true)
  if [ -n "$OWNER" ] && kill -0 "$OWNER" 2>/dev/null; then
    return 1
  fi
  STALE_LOCK="$LOCK_DIR.stale.$$"
  if mv "$LOCK_DIR" "$STALE_LOCK" 2>/dev/null; then
    rm -f "$STALE_LOCK/pid" 2>/dev/null || true
    rmdir "$STALE_LOCK" 2>/dev/null || true
  fi
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "$$" > "$LOCK_DIR/pid" 2>/dev/null || true
    return 0
  fi
  return 1
}

acquire_lock || exit 0
trap release_lock EXIT INT TERM

STATE_CMD=(
  "$PYTHON_BIN" "$DIR/bin/update-state.py"
  --state "$STATE"
  --legacy-stamp "$STAMP"
  --now "$NOW"
  --interval "$INTERVAL"
  --retry-base "$RETRY_BASE"
  --retry-max "$RETRY_MAX"
  --postflight-retry-base "$POSTFLIGHT_RETRY_BASE"
  --postflight-retry-max "$POSTFLIGHT_RETRY_MAX"
)

attempt_postflight() {
  if [ ! -f "$DIR/bin/update-postflight.py" ]; then
    "${STATE_CMD[@]}" postflight-failure --code unavailable >/dev/null 2>&1 || true
    POSTFLIGHT_MESSAGE="doctor:更新后兼容检查不可用。请决定是否立即检查。"
    return 1
  fi
  POSTFLIGHT_MESSAGE=$("$PYTHON_BIN" "$DIR/bin/update-postflight.py" 2>/dev/null)
  POSTFLIGHT_STATUS=$?
  if [ "$POSTFLIGHT_STATUS" -eq 0 ]; then
    "${STATE_CMD[@]}" postflight-success >/dev/null 2>&1 || true
    return 0
  fi
  "${STATE_CMD[@]}" postflight-failure --code "exit-$POSTFLIGHT_STATUS" >/dev/null 2>&1 || true
  [ -n "$POSTFLIGHT_MESSAGE" ] || \
    POSTFLIGHT_MESSAGE="doctor:更新后兼容检查未完成。请决定是否立即检查。"
  return 1
}

# 上次代码更新成功但 postflight 未完成时,按独立短退避补跑,不重复 merge。
if "${STATE_CMD[@]}" postflight-due >/dev/null 2>&1; then
  if attempt_postflight; then
    [ -n "$POSTFLIGHT_MESSAGE" ] && \
      append_notice "byteworker:已补跑更新后兼容检查。$POSTFLIGHT_MESSAGE"
  else
    append_notice "$POSTFLIGHT_MESSAGE"
  fi
fi

DUE_ARGS=(due)
[ "$FORCE" -eq 1 ] && DUE_ARGS+=(--force)
if ! "${STATE_CMD[@]}" "${DUE_ARGS[@]}" >/dev/null 2>&1; then
  [ -n "$NOTICE" ] && echo "$NOTICE"
  exit 0
fi
"${STATE_CMD[@]}" attempt >/dev/null 2>&1 || true

# skill 目录必须是 git 仓库 —— 否则自动更新无从做起。
# 常见于用 zip 下载、或「git init + 手工拼文件」安装的环境:不再静默,提示用户重装。
if ! git -C "$DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  "${STATE_CMD[@]}" failure --code not-git-repo >/dev/null 2>&1 || true
  append_notice "byteworker:skill 目录不是 git 仓库,自动更新已停用 —— 按 INSTALL.md 用 \`git clone\` 重装即可恢复。"
  echo "$NOTICE"
  exit 0
fi

BR=$(git -C "$DIR" symbolic-ref --short HEAD 2>/dev/null) || \
   BR=$(git -C "$DIR" rev-parse --abbrev-ref HEAD 2>/dev/null) || \
   BR="master"
if ! BEFORE=$(git -C "$DIR" rev-parse HEAD 2>/dev/null); then
  "${STATE_CMD[@]}" failure --code missing-head >/dev/null 2>&1 || true
  append_notice "byteworker:skill 仓库没有可更新的 HEAD,自动更新跳过。"
  echo "$NOTICE"
  exit 0
fi

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
  "${STATE_CMD[@]}" failure --code fetch-failed >/dev/null 2>&1 || true
  append_notice "byteworker:无法连接 GitHub,自动更新跳过(检查网络或代理设置)。"
  echo "$NOTICE"
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
  "${STATE_CMD[@]}" failure --code non-fast-forward >/dev/null 2>&1 || true
  append_notice "byteworker:本地有改动或版本已分叉,无法自动 fast-forward 更新。如需手动处理,到 $DIR 执行 git status 查看。"
  echo "$NOTICE"
  exit 0
fi

# 执行 ff merge
if ! git -C "$DIR" merge --ff-only "$REMOTE_REF" --quiet 2>/dev/null; then
  "${STATE_CMD[@]}" failure --code merge-failed >/dev/null 2>&1 || true
  append_notice "byteworker:自动更新合并失败,跳过。"
  echo "$NOTICE"
  exit 0
fi

if ! AFTER=$(git -C "$DIR" rev-parse HEAD 2>/dev/null); then
  "${STATE_CMD[@]}" failure --code missing-head-after-merge >/dev/null 2>&1 || true
  append_notice "byteworker:更新后无法读取 skill 版本,请运行 git status 检查。"
  echo "$NOTICE"
  exit 0
fi
"${STATE_CMD[@]}" success --commit "$AFTER" >/dev/null 2>&1 || true

if [ "$BEFORE" != "$AFTER" ]; then
  N=$(git -C "$DIR" rev-list --count "${BEFORE}..${AFTER}" 2>/dev/null || echo "若干")
  UPDATE_MESSAGE="byteworker skill 已自动更新(拉取 $N 个提交,本次 preflight 已加载新版本)。"
  "${STATE_CMD[@]}" postflight-pending --commit "$AFTER" >/dev/null 2>&1 || true
  if attempt_postflight; then
    append_notice "$UPDATE_MESSAGE${POSTFLIGHT_MESSAGE:+ $POSTFLIGHT_MESSAGE}"
  else
    append_notice "$UPDATE_MESSAGE $POSTFLIGHT_MESSAGE"
  fi
fi
[ -n "$NOTICE" ] && echo "$NOTICE"
exit 0
