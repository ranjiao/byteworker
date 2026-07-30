# byteworker · 自动日报 / 周报调度

> 本文件定义 harness 原生定时任务的设置、迁移、去重和无人值守边界。报告内容仍按
> `references/periodic-report.md` 生成，调度器只决定何时启动，不决定摄取哪些来源。

## 1. 共同原则

- skill 定义执行方法，Codex / Claude / TRAE 等 harness 定义时间。
- 任务必须在**知识库数据目录**本地运行；不得使用云端环境、临时 worktree 或 skill 仓库作为
  写入项目。
- 日报和周报各用一个独立任务。创建前按任务名、知识库路径与既有 prompt 查重；找到同用途任务
  时更新或接管，不新建重复任务。
- 任务只重放已经登记的 routine 来源，不在无人值守运行中扩大摄取范围、创建来源 Profile、
  发起 OAuth、切换身份、发送消息或 push。
- 自动日报**每次都先完整运行一次 routine digest**，不使用 `.last-routine-digest` 的七天提醒
  阈值跳过。自动周报同样先运行 routine digest。
- 默认时间：工作日 20:30 生成当天日报；周一 09:30 生成上一完整 ISO 周周报。安装时以
  `context.md` 时区解释，并允许用户修改日期、时间与通知偏好。

## 2. 设置状态与一次性迁移

通过机器协议读取：

```bash
python3 bin/byteworker-cli.py report-automation status --kb "<知识库绝对路径>"
```

状态保存在知识库 `state/report_automation.json`，`state/` 已加入知识库本地
`.git/info/exclude`，不进入本地回滚提交。宿主任务系统仍是真相源；该文件只记录用户选择、
任务 ID（宿主可提供时）、prompt 版本和最近运行回执。

- `needs_onboarding=true`：先调用 `decision --value prompted`，再询问一次是否启用自动日报 /
  周报。先落状态可保证用户忽略问题时也不在下次重复提示。
- 用户拒绝：调用 `decision --value declined`。
- 用户说稍后：调用 `decision --value deferred`，不在之后每次打扰；用户主动说“设置自动报告”
  时可重新进入配置。
- 用户同意：完成下面的预检、创建、首跑验证后再调用 `configure`，不得在任务尚未真实创建时
  提前记录 configured。
- `prompt_upgrade_available=true`：宿主中的任务 prompt 可能落后；先读取真实任务，再询问是否
  更新，不能仅修改本地状态伪装成功。

旧用户已有 `.kbconfig` 但没有该状态时，就是本版本的一次性迁移对象。自动更新发生在一次 skill
调用中时，新版行为按现有约定在下次调用生效；在下一次 byteworker 请求完成后询问，不打断
用户原本的业务请求。

## 3. 创建前预检

1. `.kbconfig` 已定位一个持久、私密、可写的知识库目录。
2. `context.md` 已填写时区、用户身份、职责范围、重点人物 / 项目 / 主管方向。
3. 对 routine 清单中的来源执行无副作用授权检查。需要交互登录、资源共享或服务态凭据的来源，
   先解决后再创建 active 任务。
4. 用当前 harness 在知识库目录手工执行一次日报流程，验证它能读取全局 skill、访问网络、
   写报告 / journal 并创建知识库本地提交。
5. 确认知识库没有 remote，任务不会 push 或对外发送报告。

未通过时不创建一个注定失败的 active 任务。可记录 deferred，并告诉用户具体缺口。

## 4. Harness 适配

### Codex

- 使用 standalone cron automation，不使用 thread heartbeat。
- 项目选择知识库数据目录，`executionEnvironment=local`，不能选择 worktree。
- 创建前查找同名 automation；存在则读取完整配置后更新。
- 任务需要本地文件时，电脑必须开机且 Codex 桌面应用运行。

建议名称：`byteworker · 自动日报`、`byteworker · 自动周报`。

### Claude

- 使用 Claude Code Desktop 的 **Local scheduled task**，文件夹选择知识库数据目录。
- 不使用 `/loop`：它依赖当前 session 且会过期。
- 不使用 Claude Code Remote Routine / `/schedule`：云端 fresh clone 访问不到本地私有 KB。
- 创建后在 Desktop Scheduled/Routines 页面核对任务，并执行一次 Run now。

### TRAE

- 使用 TRAE Work 桌面端“自动化”，选择**本地**运行环境和知识库数据目录。
- 不使用 TRAE Work Web / 云端环境：云端任务不能访问宿主机知识库。
- 通过“在对话中创建”或自动化面板手工创建。当前公开文档没有可依赖的稳定任务管理 API，
  不伪造 task ID；创建后以“已配置”和“执行历史”页面核验。
- 运行模式、环境和输出位置创建后不可修改；选错时删除后按正确设置重建。

其它 harness 没有经核实的原生调度能力时，只展示两份任务 prompt 和设置要求，不擅自改用
系统 cron / launchd。

官方能力参考（实现与排障时优先复核）：

- [Codex automations](https://learn.chatgpt.com/docs/automations)
- [Claude Code Desktop scheduled tasks](https://code.claude.com/docs/en/desktop-scheduled-tasks)
- [Claude Code cloud scheduled tasks](https://code.claude.com/docs/en/web-scheduled-tasks)
- [TRAE Work 自动化任务](https://docs.trae.cn/work_automated-tasks)
- [TRAE Work 本地 / 云端环境](https://docs.trae.cn/work_sandbox)

## 5. 创建与验证完成

任务 prompt 必须使用：

- `templates/report-automation-daily.md`
- `templates/report-automation-weekly.md`

任务真实创建并首跑通过后记录：

```bash
python3 bin/byteworker-cli.py report-automation configure \
  --kb "<知识库绝对路径>" \
  --harness "<codex|claude-desktop|trae-work>" \
  --timezone "<context.md 时区>" \
  --environment local \
  --daily-schedule "<用户确认的日报时间>" \
  --weekly-schedule "<用户确认的周报时间>" \
  --daily-task-id "<宿主可提供时填写>" \
  --weekly-task-id "<宿主可提供时填写>"
```

`configured` 只表示最近一次核验时真实任务存在，不保证它永远未被用户在宿主 UI 中删除。以后
重配、排障或 prompt 升级时必须重新查看宿主任务。

## 6. 无人值守失败边界

- 开始报告前获取 `report-automation lease`；若返回 `REPORT_AUTOMATION_BUSY`，本次安全退出，
  不与另一份报告或补跑并发写 KB。
- 成功生成报告并完成知识库本地提交后，调用 `complete --run-status success`。
- 取得租约后的任何失败都尽力调用 `complete --run-status failed --error-code <稳定错误码>`；
  不把宿主“进程正常退出”当成报告成功。
- OAuth、Permission Denied、分页不完整、外部来源超时、KB dirty/remote、引用无法回原文时
  按对应流程 fail closed。允许报告不存在，不允许生成无证据的假报告。
