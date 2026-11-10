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
   无人值守时在已授权范围内做可验证的决定。普通缺口记入本轮摘要，继续独立工作；
   需要确认的动作只保留为待办。只在任务级授权缺失、状态损坏或写入回滚失败时停止相关任务，
   说明需要修复的具体问题。运行期间不等用户答复。
5. `leased` 时只执行返回的一个 `job/period`，遵守 Dreaming reference 对该 job 的要求；
   `job=maintenance` 时额外加载 workflow manifest 的 `features.maintenance`。
   `job=process` 时加载 `features.routine_digest`，先调用 `dreaming process digest-scan`。
   按定期摄取细则完成全部来源的变化检查，再对有变化的来源解析普通 Digest。
   逐来源关联 Digest、保存已核验分片、登记成功或失败。卡住时先完成其它独立来源。
   用 `process digest-complete --allow-partial` 汇总实际覆盖，只有全量完成才可成功。
   收尾列出仍缺的来源、已尝试的处理和下次自动检查的范围；普通缺口使用
   `DREAMING_DIGEST_INCOMPLETE`。预算、单源权限或冲突只暂停受影响的来源。
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
   来源需暂缓时，用检查项记阶段、原因和已完成工作；严重阻塞记修复所需的条件。
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
    普通缺口只做状态说明。区分本轮运行摘要与后续定期报告。
    有 performance 时回显主要阶段、间隔和实际用量。对未知数值标记未核算。同一次唤醒续跑报告时，按执行顺序返回各任务摘要，
    以同一 DT 编号关联。报告结果另外提供 HTML 文件链接与投递状态；宿主支持本地预览时可预览。
12. 不循环领取，不创建飞书任务，不 push。除已配置的报告摘要和 maintenance 按规则
    提交的有限 doctor 决策摘要外，不发送外部消息。

启用提示已经由交互设置流程确认；runner 不重复打扰用户。机器从休眠恢复后，period 和 recovery
由 `run-due` 判定，runner 不自行补算。
