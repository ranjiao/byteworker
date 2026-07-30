# byteworker · session preflight

> 由 `SKILL.md`「操作前必读」路由到这里。日常健康路径无需读取本文件；只有 preflight 返回
> notice、用户主动检查更新或需要排查 runtime 时按本文处理。

## 唯一启动入口

```bash
bin/byteworker preflight
bin/byteworker preflight --require feishu
bin/byteworker preflight --require meego
```

preflight 合并：

- 解析 Python >=3.9 + `zoneinfo`、git/jq/bash，以及已配置/本次要求的 Node、lark-cli、meegle；
- 静默执行自动更新及真实更新后的 post-update doctor；
- 定位 `.kbconfig` 与知识库目录，验证 `context.md` / `todo.md`；
- 检查到期/临期 Todo；
- 检查自动报告 onboarding 和 prompt upgrade 状态。

已登记来源从知识库 `sources/*.json` 自动推导。`--require` 只用于即将访问但尚未登记的来源。

## 输出与 Agent 行为

- 健康：退出码 0，stdout/stderr 都为空。直接继续，不向用户汇报。
- notice：stdout 只有一行 `byteworker-session-preflight/v1` JSON。默认回执只含
  `schema_version/status/ready/kb/notices`，避免健康 runtime 细节进入 context。
- blocking：`ready=false` 且进程非零；只阻止依赖该缺口的业务，不扩大修复范围。
- `TODO_REMINDERS`：最多返回 3 项的 id/title/category/time；展示后用机器协议调用
  `todo ... mark-reminded`。
- `REPORT_AUTOMATION_ONBOARDING` / `REPORT_AUTOMATION_PROMPT_UPGRADE`：先完成当前请求，再按
  `references/report-scheduling.md` 询问；preflight 已负责一次性状态限频。
- `UPDATE_CHECK_NOTICE`：转述有限摘要；按严重程度决定是否请用户立即处理。

只有排障时运行 `bin/byteworker preflight --json`，它会额外展示 resolved executable/version。
回执不含凭据、token 或业务原文。

## runtime 规则

稳定入口 `bin/byteworker` 先选择可用 Python；库层再按以下顺序发现程序：

1. `BYTEWORKER_PYTHON_BIN`、`BYTEWORKER_NODE_BIN`、`BYTEWORKER_LARK_CLI_BIN`、
   `BYTEWORKER_MEEGLE_BIN` 显式 override；
2. 当前 PATH；
3. `~/.local/bin`、Volta、已安装 NVM 版本（新版本优先）、Homebrew 与常见系统目录。

显式 override 无效时直接失败，不静默 fallback。解析结果保留用户选中的 venv/NVM wrapper
绝对路径，不展开 symlink 后绕开原环境。`bin/byteworker lark ...`、
`bin/byteworker meegle ...`、`bin/byteworker run <command> ...` 都继承同一环境。

依赖检查不等于登录检查，不发起 OAuth；具体来源仍在 `source auth-status/inspect/capture`
执行 Auth Guard。

## 自动更新内部语义

- 成功检查后 7 天内不重复 fetch；失败按短周期指数退避。
- 跨进程锁与 `.update-state.json` 保存检查、失败和 postflight 状态。
- 只有代码 HEAD 真实变化后才运行 post-update doctor；未完成的 doctor 独立重试，不重复 merge。
- doctor 有无法自动处理的 error、修复失败或文件正在编辑时，notice 视为严重问题；
  只有 warning/info 时只给数量摘要。
- 无 GitHub 账号/SSH key 也可通过 public HTTPS fallback fetch；默认不改 origin。
- `BYTEWORKER_NO_AUTO_UPDATE=1` 可停用自动更新。

用户主动说“更新 skill / 检查更新 / byteworker 有新版吗”时运行：

```bash
bin/byteworker run bin/update-check.sh --force
```

排障时查看：

```bash
bin/byteworker update-status
bin/byteworker deps
```
