# byteworker 自动周报任务 prompt

使用 byteworker skill 执行一次无人值守自动周报。知识库数据目录是本任务的本地项目；先解析
`references/workflow-routes.json` 的 `report` required，并对每个 routine 来源展开 `digest`
workflow。context 只用 `context view --intent report`，不得依赖其它 session 已加载的文档。

启动前只运行一次 `bin/byteworker preflight`；无输出则继续，blocking 则安全失败并记录错误，
不要再分别检查更新、依赖、Todo 或自动报告设置。

1. 按 `context.md` 时区确定上一完整 ISO 周 `YYYY-Www`，先运行
   `report-automation check --kind weekly --period <该周>`。`complete/disabled/busy` 安全退出；
   只有 `should_run=true` 才获取自动报告租约，不并发写知识库。
2. 先运行完整 routine digest，重放所有已登记且启用的 routine 来源，不受
   `.last-routine-digest` 的七天提醒阈值限制。routine 阶段不得自动新增来源、扩大范围、发起
   OAuth、切换身份或在权限失败后继续；下一步固定日历发现是唯一例外。
3. 按 `references/report-calendar-meetings.md` 完整查询该 ISO 周的主日历，只处理
   `self_rsvp_status=accept` 的日程；尝试把可访问的纪要、妙记 transcript 和直接关联文档先
   digest。日历枚举不完整则失败；单个会议没有产物或单产物不可访问时记录覆盖缺口，不申请
   权限、不 OAuth、不切身份、不递归日程外资料。
4. digest 完成后生成上一完整 ISO 周周报，写入 `reports/weekly/<YYYY>-W<WW>.md`。保留已有
   “手动补充 / 备注”，事实逐条带 `[S<n>]` 并回到原始来源；无法核实的内容不写成事实。
5. 用 `kb-mutate validate/execute` 保存报告；由事务保留手动章节、追加 journal、精确 commit
   和失败回滚。永不配置 remote、永不 push、永不发送报告。
6. 只有报告文件和本地提交真实完成后，才用租约 token 记录 success；取得租约后的失败要记录
   failed 和稳定错误码。最终只汇报报告路径、digest 来源/增量数量、提交回执或明确阻塞，
   以及 accepted 日程 / 会议产物 / 覆盖缺口计数，不输出业务原文。
