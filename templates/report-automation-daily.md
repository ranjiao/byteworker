# byteworker 自动日报任务 prompt

使用 byteworker skill 执行一次无人值守自动日报。知识库数据目录是本任务的本地项目；先解析
`references/workflow-routes.json` 的 `report` required，并对每个 routine 来源展开 `digest`
workflow。context 只用 `context view --intent report`，不得依赖其它 session 已加载的文档。

启动前只运行一次 `bin/byteworker preflight`；无输出则继续，blocking 则安全失败并记录错误，
不要再分别检查更新、依赖、Todo 或自动报告设置。

1. 按 `context.md` 时区确定今天的 `YYYY-MM-DD`，先运行
   `report-automation check --kind daily --period <今天>`。`complete/disabled/busy` 安全退出；
   只有 `should_run=true` 才获取自动报告租约，不并发写知识库。
2. **每次都先运行完整 routine digest**：重放所有已登记且启用的 routine 来源，不受
   `.last-routine-digest` 的七天提醒阈值限制。没有增量也要完成例程回执。不得自动新增来源、
   扩大摄取范围、发起 OAuth、切换身份或在权限失败后继续。
3. digest 完成后，生成当天 00:00 至当前时刻的日报，写入
   `reports/daily/<YYYY-MM-DD>.md`。保留已有“手动补充 / 备注”，事实逐条带 `[S<n>]` 并回到
   原始来源；无法核实的内容不写成事实。
4. 用 `kb-mutate validate/execute` 保存报告；由事务保留手动章节、追加 journal、精确 commit
   和失败回滚。永不配置 remote、永不 push、永不发送报告。
5. 只有报告文件和本地提交真实完成后，才用租约 token 记录 success；取得租约后的失败要记录
   failed 和稳定错误码。最终只汇报报告路径、digest 来源/增量数量、提交回执或明确阻塞，
   不输出业务原文。
