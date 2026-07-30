# byteworker 自动报告补偿任务 prompt

使用 byteworker skill 执行一次无人值守的自动报告缺口检查。知识库数据目录是本任务的本地项目；
严格按 skill 自己的 `.kbconfig`、`context.md`、`references/report-scheduling.md` 和
`references/periodic-report.md` 执行。

启动前只运行一次 `bin/byteworker preflight`；无输出则继续，blocking 则安全退出，不要再分别
检查更新、依赖、Todo 或自动报告设置。

1. 读取 `report-automation status` 和 `context.md` 时区。只检查已经启用的日报/周报，不创建或
   修改宿主任务，不扩大 routine 来源。
2. 建立有限候选：日报检查最近 5 个应生成报告的工作日；今天只有在已过
   `daily.schedule` 后才纳入。周报只检查上一完整 ISO 周。候选从新到旧排列。
3. 对候选逐个调用 `report-automation check --kind <daily|weekly> --period <period>`。
   `complete/disabled/busy` 直接跳过；找到第一个 `should_run=true` 的候选后停止检查，本次最多
   补跑一期，避免一轮长时间占用本地知识库。
4. 对命中的一期执行对应自动日报/周报完整流程：先取租约，再完整运行 routine digest，随后生成
   报告、journal 和知识库本地 Git 回滚提交。不得在没有网络、授权失败、分页不完整或证据不足时
   生成残缺报告。
5. 成功后记录 `complete --run-status success`；取得租约后的失败记录
   `complete --run-status failed --error-code <稳定错误码>`。后续补偿检查会根据
   `last_success` 决定是否仍需重试。没有缺口时只报告 `no-op`，不写知识库、不创建提交。

补偿任务永不发送报告、永不 push、永不使用云端或 worktree。不要假定 Codex 会补跑错过的触发；
本任务自身的周期唤醒才是离线恢复后的补偿入口。
