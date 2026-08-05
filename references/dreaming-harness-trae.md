# Dreaming · TraeWork 定时任务接入

当前运行环境或用户选择的 Dreaming 宿主属于 TRAE 产品家族时加载。该文件是 harness 兼容层，
负责区分 TraeWork 与 TRAE IDE/TraeCode，不改变 Dreaming scheduler、job schedule 或业务处理
语义。

## 识别与边界

先识别具体产品，不得把名称中的 `TRAE` 当成支持定时任务的充分条件：

- **TraeWork**：独立的 TraeWork 桌面版，旧名称可能显示为 TRAE SOLO；可进入后续接入流程。
- **TRAE IDE/TraeCode**：代码编辑器及其内置 SOLO 模式；不能创建 Dreaming 所需的本地定时
  任务，必须停止接入并提示用户切换到 TraeWork 桌面版。
- **无法判断**：先用自然语言询问用户当前使用的是 TraeWork 还是 TRAE IDE/TraeCode；确认前
  不创建任务，也不执行 `dreaming harness register`。

官方文档将 TraeWork 定义为独立于 TRAE IDE 运行的应用；TraeWork 的“自动化”支持按时间或频率
执行任务，并配置 Work/Code 模式与云端/本地环境。TRAE IDE 的任务管理只描述交互式创建和并行
任务，不提供该自动化入口。官方说明：

- <https://docs.trae.cn/work_what-is-trae-solo>
- <https://docs.trae.cn/work_automated-tasks>
- <https://docs.trae.cn/ide_task-management>

即使当前会话暴露 Schedule 工具，也必须先确认产品是 TraeWork；在 TRAE IDE/TraeCode 中不得
调用该工具创建 Dreaming 任务。TraeWork 会话没有真实工具回执时，禁止猜内部接口、修改应用
私有配置、用 `launchd/cron` 冒充 Agent task，或直接执行 `dreaming harness register`。

Dreaming 需要用户本地 KB、用户态 lark-cli 和 Agent 模型分析，因此必须创建**本地 Agent
任务**。TraeWork 网页版仅提供云端运行环境，不能访问这些本机状态，不作为等价替代；用户必须
使用 TraeWork 桌面版。

## TRAE IDE/TraeCode 中的提示

检测到 TRAE IDE、TraeCode 或其内置 SOLO 模式时，不展示创建步骤，直接使用自然语言说明：

> 当前 TRAE IDE 不能创建后台信息助手所需的本地定时任务。请改用 TraeWork 桌面版打开同一
> 知识库目录，再继续设置自动运行。

同时保持“自动运行：待完成”。不得把当前对话继续运行、IDE Hook、shell cron 或 launchd 描述为
等价能力，也不得登记虚构 task id。

## 必须提示用户的操作

确认当前产品是 TraeWork 桌面版后，如果会话没有可调用的 Schedule 工具，启用后必须立即告诉
用户：
“设置已经保存，但自动运行还没有接通；完成下面的本地定时任务后，助手才会按时工作。”
不得向用户输出 `enabled=true`、`operational=false` 或其它内部诊断串。然后提供以下步骤：

1. 打开 TraeWork 桌面版左侧的“自动化”，点击“手动新建”或“在对话中创建”。
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

6. 本地任务要求电脑开机、唤醒、联网。需要夜间运行时，提示用户检查 TraeWork 的防睡眠/设备
   在线设置；不要承诺休眠期间按时执行。
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

如果 UI 中没有“自动化”入口，先判断是否误用了 TRAE IDE/TraeCode；是则提示切换到 TraeWork
桌面版，不是则提示检查 TraeWork 版本。不要引导到 TraeWork 网页版执行本地 Dreaming。

## Prompt 与日志安全

- Prompt 只放仓库路径、KB 路径、稳定 TASK_ID，不放 lark token、消息正文或其它凭据。
- TraeWork 自动化面板的运行历史是宿主审计；Dreaming 的 `runs list/show/tail` 是内部阶段
  审计，两者都需要保留，不能互相冒充。
- 首次 Run now 失败时保持 `harness.status=pending`。修复后重新触发；不得先 register 再等待
  将来成功。
