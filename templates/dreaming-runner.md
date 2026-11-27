# Byteworker Dreaming Runner

这是用户明确启用的本地 Dreaming 定时任务。不要改变 Dreaming 设置，也不要处理未授权来源。

1. 首个命令调用 `bin/byteworker dreaming tick --kb "<KB>" --owner "<TASK_ID>"`，保存 `tick_id`。
   该入口先记唤醒并独立检查到期日报与可补充版本，再执行一次 `preflight --unattended` 和 `run-due`。
   返回的 `daily.published` 可直接展示本地归档路径。若日报失败，回显事故原因，即使其它任务仍可运行。
   无人值守不展示能力 Tip，
   不调用 `discover recommend`。不要重复启动检查或领取。
2. 启动失败时说明哪项工作没能开始及已知原因。诊断编号、阶段和错误码保存在本地日志，按需排查。
3. `disabled/idle/busy` 时安静结束。
4. `leased` 后按租约选择 feature：maintenance → maintenance，process → routine_digest，recovery → recovery，morning/daily/weekly → report。
   运行 `bin/byteworker workflow-budget resolve --workflow dreaming --feature <feature>`，只读 `references/workflow-routes.json` 解析出的规则。
   无人值守时在已授权范围内做可验证的决定。普通缺口记入本轮摘要，继续独立工作；
   需要确认的动作只保留为待办。只在任务级授权缺失、状态损坏或写入回滚失败时停止相关任务，
   说明需要修复的具体问题。运行期间不等用户答复。
5. `leased` 时只执行返回的一个 `job/period`，遵守 Dreaming reference 对该 job 的要求；
   maintenance 按维护规则完成心跳、扫描和租约收尾后结束本轮。
   process 先调用 `dreaming process digest-scan`，按 routine_digest 规则逐源处理，再核验批次。
   对本轮 IM 窗口重复 prepare / commit 至 covered，无法继续时按 partial 记录具体缺口。
   `job=recovery` 补 IM 缺口时只用 `lease.im_recovery.window`。窗口为空时继续检查其它恢复项，
   没有可恢复项就成功结束。若窗口在执行时到期，记为跳过并继续其它恢复项。
6. 保存 lease 的 `run_id`。进入 collection/analysis/consolidation/action/report/maintenance/
   recovery 阶段时调用 `dreaming heartbeat`。process 心跳会延长有效租约，不能复活已过期租约。
   单阶段超过 60 秒时至少再写一次心跳；派发预计耗时的调用前先写心跳。
   `detail-code` 只用有限机器码，禁止放业务正文。
7. 所有来源操作只调用 `bin/byteworker` 公开命令；禁止直接 import digest/query 内部模块。
8. process / maintenance / recovery 的成功、部分或失败路径调用通用 `dreaming complete`。
   已打开定期摄取批次时，普通失败和部分完成都先完成批次收尾。
   process 只有在 `process digest-complete` 成功后才允许 `run-status=success`；还须覆盖本次消息时段。若返回 DREAMING_IM_INCOMPLETE 或 DREAMING_REPORT_BLOCKED，按所列缺口继续本次任务。即使 IM grant 为
   `off`，已启用的普通 digest 来源也必须处理。没有定期来源时该步骤成功 no-op。
   morning / daily / weekly 在报告无法归档时才调用通用 `dreaming complete`。
   自动日报在计划时间到达后直接整理已核实材料。消息与普通来源有缺口时按 partial 覆盖归档，仍走报告成功路径。`partial/failed` 提供稳定错误码。已知时传
   `--item-count/--finding-count/--gap-count`，不得用字符串 checkpoint 代替可结构化计数。
   process 或 recovery 已采集本轮消息时还必须传 `--batch-id "<EB-...>"`，供运行审计关联本轮输入、
   FindingBundle 和 evidence。
   每个由通用 `complete` 结束的任务都要把本轮主要信息写入 KB 外临时 JSON，并传 `--result-input`。
   `summary` 写给知识库主人：用一两句说清哪件事有了什么新信息、涉及谁或什么决定、还有什么没核实。
   历史消息写原消息日期。只有消息时写消息条数和实际发现，区分消息提到的文档与亲自读过的文档。
   `source_notes` 可按 source_key 写各来源的重要变化，失败或暂缓写具体原因及影响。
   文档数量、逐项状态和已修改的节点由系统按回执补齐。只从实际改动说明节点补充了什么。
   内部执行步骤、工具名、诊断编号和未执行的动作留在技术记录。结果结构为：
   `{"schema_version":"byteworker-dreaming-run-result/v1","job":"<job>",`
   `"period":"<period>","summary":"本轮做了什么及结论",`
   `"checks":[{"name":"<检查项>","status":"pass|warning|fail|noop","detail":"<结果>"}],`
   `"repairs":[{"path":"<KB 相对路径>","code":"<问题码>","action":"<修复动作>","detail":"<修复结果>"}]}`。
   没有自动修复时 `repairs` 写空数组；不得写业务正文、完整 diff、URL token 或 stdout/stderr。
   来源需暂缓时，用检查项记阶段、原因和已完成工作；严重阻塞记修复所需的条件。
   maintenance 成功回执必须按维护规则附最终 Doctor 扫描的完整 `issues`。仍有待办时也按成功
   完成扫描；宿主只汇总完成回执的 `maintenance.new`。
9. 若本轮成功完成 `job=process` 且 `period=catchup:*`，立即再调用一次：
   `bin/byteworker dreaming run-due --kb "<KB>" --owner "<TASK_ID>" --followup-after-run-id "<run_id>" --tick-id "<tick_id>"`。
   领取到报告后先解析 report feature，再执行第 10 步。disabled/idle/busy 时结束。
10. morning/daily/weekly 按 `references/dreaming-reports.md` 一次生成结构化报告 JSON 后，
    必须调用 `dreaming report complete`。该命令会渲染 summary、内部 Markdown、自包含 HTML、
    `reports/<kind>/<period>.md` 归档快照，完成 lease，并按配置投递摘要；它是报告成功路径唯一
    的完成调用，之后禁止再调用通用 `dreaming complete`。不要再分开调用
    `report render`、`complete`、`enqueue-delivery` 和 `report deliver`。
11. process 完成或部分完成后，可立即调用 `dreaming report publish-due --kb "<KB>" --supplement` 更新迟到资料。
    以 `summary.brief`（报告为 `run.summary.brief`）回显本轮任务。先写具体发现，再写文档成败、
    节点变更和剩余缺口。若续跑报告，附报告结论、HTML 链接和投递状态。
12. 报告归档后结束本轮。不创建飞书任务，不 push。除已配置的报告摘要和 maintenance 按规则
    提交的有限 doctor 决策摘要外，不发送外部消息。

启用提示已经由交互设置流程确认；runner 不重复打扰用户。机器从休眠恢复后，period 和 recovery
由 `run-due` 判定，runner 不自行补算。
