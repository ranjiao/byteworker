# Dreaming · TRAE 产品分流与本地任务接入

只在当前环境或用户选择的宿主属于 TRAE 产品家族时加载。
不得把名称中的 `TRAE` 当成支持定时任务的充分条件；先识别具体产品，再决定是否继续。

## 产品边界

- **TRAE IDE/TraeCode**（包括内置 SOLO 模式）不提供 Dreaming 所需的本地自动化任务。
  提示用户切换到 TraeWork 桌面版；即使当前会话暴露 Schedule 工具，也不创建任务，也不执行
  `dreaming harness register`。
- **TraeWork 网页版仅提供云端运行环境**，不能访问用户本机 KB、用户态 lark-cli 和本地日志，不是
  等价替代；同样不创建或登记 Dreaming 任务。
- 只有确认处于 **TraeWork 桌面版** 且可以创建本地 Code 任务时，才继续下面步骤。没有真实工具或
  UI 回执时保持 pending，禁止猜内部 API、修改应用私有配置或用 cron/launchd 冒充 Agent task。

## Sandbox 前置条件

创建任务前必须确认 **KB 绝对路径已经作为工作目录加入当前 TraeWork 项目**。只选择任务工作目录、
填写 `.kbconfig` 或传 `--kb` 不会扩大 TraeWork Sandbox 的目录访问范围。遇到
`Operation not permitted` / `Permission denied` 时先让用户在项目中加入 KB；不要用 `sudo`、`chmod`、
复制 KB 或反复重试绕过权限。

## 本地任务步骤

当前会话没有可调用的 TraeWork Schedule 工具时，告诉用户：“设置已经保存，但自动运行还没有接通；
完成下面的本地定时任务后，助手才会按时工作。”不得向用户输出 `enabled=true`、
`operational=false`、harness 或其它内部诊断串。

1. 打开 TraeWork 桌面版“自动化”面板，创建任务，稳定名称为 `byteworker-dreaming-local`。
2. 选择 **Code 模式**、**本地环境**，工作目录使用已经加入当前项目的 KB。
3. 询问并确认**本地任务唤醒间隔**，推荐 2 小时；用户可选择其它合理间隔，不得静默套用。唤醒只
   检查是否有 job 到期，不等于每次都调用模型；业务 schedule 仍以用户已确认的配置为准。
4. Prompt 使用：

```text
读取 <BYTEWORKER_REPO>/templates/dreaming-runner.md，严格按其中流程执行。
byteworker 仓库路径为 <BYTEWORKER_REPO>，KB 路径为 <KB>，
TASK_ID 使用 byteworker-dreaming-local。
```

5. 提示本地任务依赖电脑开机、唤醒、联网，不承诺休眠期间按时执行。
6. 创建后点击一次 **Run now**，看到真实任务记录，并确认没有等待权限或用户输入。
7. 只有用户确认任务存在且首次触发完成后，才运行：

```bash
bin/byteworker dreaming harness register --kb "<KB>" \
  --task-id "byteworker-dreaming-local"
bin/byteworker dreaming status --kb "<KB>"
```

8. 本地任务已登记、自动运行状态通过且首次触发时间非空时，才显示“自动运行：已接通”；否则显示
   “自动运行：待完成”。首次 Run now 失败时保持 pending，修复后重新触发，不得先 register。

## Prompt 与日志安全

- Prompt 只放仓库路径、KB 路径、稳定 TASK_ID，不放 lark token、消息正文或凭据。
- TraeWork 任务历史与 Dreaming `runs list/show/tail` 都保留，不能互相冒充。
- 任务或登记失败只影响 Dreaming，不阻塞 digest/search/update 等前台能力。
