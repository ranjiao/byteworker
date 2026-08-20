# Dreaming · TRAE 定时任务接入

只在当前运行环境或用户选择的 Dreaming 宿主是 TRAE 时加载。该文件是 harness 兼容层，不改变
Dreaming scheduler、job schedule 或业务处理语义。

## 识别与边界

满足任一条件时视为 TRAE：

- 当前会话由 TRAE / TRAE SOLO / TraeWork 承载；
- 用户明确选择 TRAE 作为宿主；
- `owner_harness` 配置为 `trae` 或 `trae-*`。

TRAE SOLO/TraeWork 的桌面端和网页端支持定时自动化任务，可按时间/频率执行 Prompt，并选择本地
或云端环境。官方说明：

- <https://forum.trae.cn/t/topic/15182>
- <https://forum.trae.cn/t/topic/15566>

但当前 Agent 会话不一定暴露 Schedule 工具或定时任务 API。没有真实工具回执时，禁止猜内部
接口、修改 TRAE 私有配置、用 `launchd/cron` 冒充 Agent task，或直接执行
`dreaming harness register`。

Dreaming 需要用户本地 KB、用户态 lark-cli 和 Agent 模型分析，因此必须创建**本地 Agent
任务**；云端任务不能访问这些本机状态，不作为等价替代。

## 必须提示用户的操作

如果当前会话没有可调用的 TRAE Schedule 工具，启用后必须立即告诉用户：
“设置已经保存，但自动运行还没有接通；完成下面的本地定时任务后，助手才会按时工作。”
不得向用户输出 `enabled=true`、`operational=false` 或其它内部诊断串。然后提供以下步骤：

1. 打开 TRAE SOLO/TraeWork 桌面端的任务管理面板，点击 `+ 新任务` 或创建定时/自动化任务。
2. 任务名使用稳定名称 `byteworker-dreaming-local`。
3. 选择 **Code 模式**、**本地环境**；工作目录选择用户的 KB 绝对路径。
4. 触发频率设为**每 30 分钟**。这是检查是否有工作到期，不等于每 30 分钟调用模型；真正的
   自动检查、定时摘要、健康检查和离线补跑时间仍按用户刚刚确认的计划执行。
5. Prompt 使用：

```text
读取 <BYTEWORKER_REPO>/templates/dreaming-runner.md，严格按其中流程执行。
byteworker 仓库路径为 <BYTEWORKER_REPO>，KB 路径为 <KB>，
TASK_ID 使用 byteworker-dreaming-local。
```

6. 本地任务要求电脑开机、唤醒、联网。需要夜间运行时，提示用户检查 TRAE 的防睡眠/设备在线
   设置；不要承诺休眠期间按时执行。
7. 创建后在任务面板点击一次“触发任务/Run now”。必须看到任务记录，并确认没有等待权限或用户
   输入的步骤。
8. 用户确认任务已存在且首次触发完成后，才运行：

```bash
bin/byteworker dreaming harness register --kb "<KB>" \
  --task-id "byteworker-dreaming-local"
bin/byteworker dreaming status --kb "<KB>"
```

9. 内部只有在本地定时任务已登记、自动运行状态通过，且首次触发时间非空时，才可以向用户显示
   “自动运行：已接通”。否则显示“自动运行：待完成”，不得暴露内部字段。

如果 UI 中没有自动化任务入口，提示用户确认正在使用支持定时任务的 TRAE SOLO/TraeWork
桌面端或网页端。不要把普通 IDE 的 Agent 会话、当前对话继续运行或 shell cron 描述为同一能力。

## Prompt 与日志安全

- Prompt 只放仓库路径、KB 路径、稳定 TASK_ID，不放 lark token、消息正文或其它凭据。
- TRAE 任务面板的运行历史是宿主审计；Dreaming 的 `runs list/show/tail` 是内部阶段审计，两者
  都需要保留，不能互相冒充。
- 首次 Run now 失败时保持 `harness.status=pending`。修复后重新触发；不得先 register 再等待
  将来成功。
