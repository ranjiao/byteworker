# Byteworker Dreaming Runner

这是用户明确启用的本地 Dreaming 定时任务。不要改变 Dreaming 设置，也不要处理未授权来源。

1. 首个命令调用 `bin/byteworker dreaming tick --kb "<KB>" --owner "<TASK_ID>"`，保存 `tick_id`。
   该入口先记唤醒，再执行一次 `preflight --unattended` 和 `run-due`。无人值守不展示能力 Tip，
   不调用 `discover recommend`。不要重复启动检查或领取。
2. 领取到任务或启动失败时，取得编号后就在宿主任务的用户可见输出中回显。最终结果首行保留
   状态、唤醒编号 `DT-…`、已取得的运行编号 `DR-…`。失败、部分完成或待投递时附上已记录的阶段和固定原因码。
   例如 `后台分析失败 · DT-… · DR-… · DIGEST_FLOW_COMMIT_FAILED`。只有最终输出的宿主也遵循此格式。
   `preflight_failed/scheduler_failed` 回显已取得的 `DT-…` 和失败阶段后结束。若未取得编号，写明编号未生成。
   这行仅含技术信息，不拼接知识库正文、路径、凭据或原始命令输出，也不为此另发通知。
3. `disabled/idle/busy` 时安静结束。
4. `leased` 后从 `references/workflow-routes.json` 解析 `dreaming` 并读取 required 闭包。
5. `leased` 时只执行返回的一个 `job/period`，遵守 Dreaming reference 对该 job 的要求；
   `job=maintenance` 时额外加载 workflow manifest 的 `features.maintenance`。
   `job=process` 时必须额外加载 `features.routine_digest`，先调用
   `dreaming process digest-prepare --token "<lease.token>"` 获取本轮全部已启用定期来源快照。
   对清单逐源执行其原有普通 digest 工作流，不缩减依赖、语义、冲突、候选、引用或事务步骤：
   普通 Digest 取得 `run_id` 后立刻调用 `dreaming heartbeat --token "<lease.token>"
   --stage collection --digest-run-id "<DIGEST_RUN_ID>"`，同时传 `--kb`。即使子运行后来失败，
   也保留显式关联。Wiki 多页同样在各页普通 Digest 启动后记录。Profile 来源按 `source_type/source_uid` 重放；`origin=legacy_raw` 时按返回的历史 raw 定位兼容
   配置；Wiki 子树执行原有完整扫描和变化展示，变化页面仍按普通文档 digest。
   `feishu_chat` 的 `source capture` 已直接产出 SourceBundle，只传 `--out <bundle.json>`，
   不得再传 `--bundle-out`；只有先产出独立 capture JSON 的来源才同时使用两者。
   群聊 `digest-flow prepare` 若返回 `receipt.status=empty_window`，以该回执调用
   `process digest-record`（结果 `status=empty_window`），再继续下一个来源。此时没有节点或提交计划。
   超预算或 planner 返回 `budget_required=true` 时，读取 `references/workflow-budgets.md`，
   先核算实际分片和总消耗。复用已有 analysis packet 和预算分片，按角色闭包分批处理及归并，
   每个 parallel worker 使用全新隔离上下文。不要重抓已验证的材料、把 `inline` 当作预算通过或截断来源。
   确需用户决定时，保留私有产物，以 `DREAMING_BUDGET_REVIEW_REQUIRED` 完成并释放租约。
   宿主结果首行显示「等待预算确认」与诊断编号，随后列出超限项、估算量、当前上限、已尝试的拆分及
   建议的一次性放宽值或预计总消耗，请用户选择继续、调整范围或暂缓。宿主容纳不下时说明可行的
   拆分或宿主选项，不建议提高软件上限。技术故障按实际原因报告并给下一步，不冒充待确认。
   分片通过预算后继续逐片语义分析和归并。工具或执行受阻时，报告实际阶段、原因和下一步。
   依赖范围或内容冲突确实需要用户裁决时，按普通 Digest 规则记录等待原因，保留已核验的私有材料。
   用 `SOURCE_REVIEW_REQUIRED` 和带 `review` 的 `--result-input` 完成并释放租约。具体写出发现了
   哪个范围问题或冲突、为什么需要用户决定，给出可行操作及其后果。问题未定位时继续核查；
   无法继续时按实际执行阻塞结束。
   每个普通来源取得 DigestTxn `committed/noop` 或群聊 `empty_window` 回执后，把
   `byteworker-dreaming-digest-result/v1` 写入 KB 外临时文件并调用 `process digest-record`；
   Wiki 完整扫描使用 `status=observed` 和真实 scan receipt。全部记录后调用
   `process digest-complete`。任一来源授权、完整性、语义或事务失败时，本轮 process 必须失败，
   不得推进来源覆盖检查点。
6. 保存 lease 的 `run_id`。进入 collection/analysis/consolidation/action/report/maintenance/
   recovery 阶段时调用 `dreaming heartbeat`；单阶段超过 60 秒时至少再写一次 heartbeat。
   `detail-code` 只用有限机器码，禁止放业务正文。
7. 所有来源操作只调用 `bin/byteworker` 公开命令；禁止直接 import digest/query 内部模块。
8. process / maintenance / recovery 的成功、部分或失败路径调用通用 `dreaming complete`。
   process 只有在 `process digest-complete` 成功后才允许 `run-status=success`；即使 IM grant 为
   `off`，已启用的普通 digest 来源也必须处理。没有定期来源时该步骤成功 no-op。
   morning / daily / weekly 只有在报告生成前失败或只能 partial 时才调用通用
   `dreaming complete`；报告成功路径不得调用它。`partial/failed` 提供稳定错误码。已知时传
   `--item-count/--finding-count/--gap-count`，不得用字符串 checkpoint 代替可结构化计数。
   process 已生成 EvidenceBatch 时还必须传 `--batch-id "<EB-...>"`，供运行审计关联本轮输入、
   FindingBundle 和 evidence。
   每个由通用 `complete` 结束的任务都要把本轮主要信息写入 KB 外临时 JSON，并传 `--result-input`。
   只总结有证据的发现、变化和阻塞。process 可用 `source_notes` 为已登记来源补充简短发现，
   每项含 `source_key` 和 `summary`。没有完成的来源不得概括为「无新增」。结果结构为：
   `{"schema_version":"byteworker-dreaming-run-result/v1","job":"<job>",`
   `"period":"<period>","summary":"本轮做了什么及结论",`
   `"checks":[{"name":"<检查项>","status":"pass|warning|fail|noop","detail":"<结果>"}],`
   `"repairs":[{"path":"<KB 相对路径>","code":"<问题码>","action":"<修复动作>","detail":"<修复结果>"}]}`。
   没有自动修复时 `repairs` 写空数组；不得写业务正文、完整 diff、URL token 或 stdout/stderr。
   待来源复核时，结果文档必须额外提供
   `review: {stage, problem, question, options: [{action, effect}]}`。
   `stage` 为 `dependency_review` 或 `conflict_review`，给出 2 至 5 个不同选项，可包括暂缓。
   若完成命令因字段缺失拒绝，依据现有证据修正后用同一租约完成。
   maintenance 成功回执必须按维护规则附最终 Doctor 扫描的完整 `issues`。仍有待办时也按成功
   完成扫描；宿主只汇总完成回执的 `maintenance.new`。
9. 若本轮成功完成 `job=process` 且 `period=catchup:*`，立即再调用一次：
   `bin/byteworker dreaming run-due --kb "<KB>" --owner "<TASK_ID>" --followup-after-run-id "<run_id>" --tick-id "<tick_id>"`。
   只处理返回的 morning/daily/weekly；`disabled/idle/busy` 安静结束。本轮最多这一个 follow-up。
10. morning/daily/weekly 按 `references/dreaming-reports.md` 一次生成结构化报告 JSON 后，
    必须调用 `dreaming report complete`。该命令会渲染 summary、内部 Markdown、自包含 HTML、
    `reports/<kind>/<period>.md` 归档快照，完成 lease，并按配置投递摘要；它是报告成功路径唯一
    的完成调用，之后禁止再调用通用 `dreaming complete`。不要再分开调用
    `report render`、`complete`、`enqueue-delivery` 和 `report deliver`。
11. 将完成回执中的 `summary`（报告为 `run.summary`）用自然语言返回给用户。首行按步骤 2，
    随后给出主要信息、各来源分布、实际耗时、已核验的知识库变更和仍未覆盖的范围。
    有 `summary.review` 时，首行显示「等待来源复核」，随后直接给出具体问题、要用户决定的事项，
    按编号列出操作及其影响，请用户回复编号或说明处理范围。区分本轮运行摘要与后续定期报告。
    对未知数值标记未核算，不估算写入数。同一次唤醒续跑报告时，按执行顺序返回各任务摘要，
    以同一 DT 编号关联。报告结果另外提供 HTML 文件链接与投递状态；宿主支持本地预览时可预览。
12. 不循环领取，不创建飞书任务，不 push。除已配置的报告摘要和 maintenance 按规则
    提交的有限 doctor 决策摘要外，不发送外部消息。

启用提示已经由交互设置流程确认；runner 不重复打扰用户。机器从休眠恢复后，period 和 recovery
由 `run-due` 判定，runner 不自行补算。
