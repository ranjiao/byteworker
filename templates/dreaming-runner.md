# Byteworker Dreaming Runner

这是用户明确启用的本地 Dreaming 定时任务。不要改变 Dreaming 设置，也不要处理未授权来源。

1. 在 byteworker skill 仓库运行一次 `bin/byteworker preflight`。
2. 从 `references/workflow-routes.json` 解析 `dreaming` workflow，并读取其完整 required 闭包。
3. 调用 `bin/byteworker dreaming run-due --kb "<KB>" --owner "<TASK_ID>"`。
4. `disabled/idle/busy` 时安静结束。
5. `leased` 时只执行返回的一个 `job/period`，遵守 Dreaming reference 对该 job 的要求；
   `job=maintenance` 时额外加载 workflow manifest 的 `features.maintenance`。
   `job=process` 时必须额外加载 `features.routine_digest`，先调用
   `dreaming process digest-prepare --token "<lease.token>"` 获取本轮全部已启用定期来源快照。
   对清单逐源执行其原有普通 digest 工作流，不缩减依赖、语义、冲突、候选、引用或事务步骤：
   Profile 来源按 `source_type/source_uid` 重放；`origin=legacy_raw` 时按返回的历史 raw 定位兼容
   配置；Wiki 子树执行原有完整扫描和变化展示，变化页面仍按普通文档 digest。
   每个普通来源取得 DigestTxn `committed/noop` 后，把
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
   maintenance/recovery 还必须把本轮结论写入 KB 外临时 JSON，并传 `--result-input`。结构为：
   `{"schema_version":"byteworker-dreaming-run-result/v1","job":"<job>",`
   `"period":"<period>","summary":"本轮做了什么及结论",`
   `"checks":[{"name":"<检查项>","status":"pass|warning|fail|noop","detail":"<结果>"}],`
   `"repairs":[{"path":"<KB 相对路径>","code":"<问题码>","action":"<修复动作>","detail":"<修复结果>"}]}`。
   没有自动修复时 `repairs` 写空数组；不得写业务正文、完整 diff、URL token 或 stdout/stderr。
9. 若本轮成功完成 `job=process` 且 `period=catchup:*`，立即再调用一次：
   `bin/byteworker dreaming run-due --kb "<KB>" --owner "<TASK_ID>" --followup-after-run-id "<run_id>"`。
   只处理返回的 morning/daily/weekly；`disabled/idle/busy` 安静结束。本轮最多这一个 follow-up。
10. morning/daily/weekly 按 `references/dreaming-reports.md` 一次生成结构化报告 JSON 后，
    必须调用 `dreaming report complete`。该命令会渲染 summary、内部 Markdown、自包含 HTML、
    `reports/<kind>/<period>.md` 归档快照，完成 lease，并按配置投递摘要；它是报告成功路径唯一
    的完成调用，之后禁止再调用通用 `dreaming complete`。不要再分开调用
    `report render`、`complete`、`enqueue-delivery` 和 `report deliver`。
11. 任务结果必须回显 300-500 字 summary 和 HTML 绝对路径；宿主支持本地产物预览时可直接预览，
    否则返回可点击文件链接。不得调用宿主私有 HTML API。飞书失败不删除本地产物，也不得冒充
    送达。
12. 不循环领取，不创建飞书任务，不 push。除已配置的报告摘要和 maintenance 按规则
    提交的有限 doctor 决策摘要外，不发送外部消息。

启用提示已经由交互设置流程确认；runner 不重复打扰用户。机器从休眠恢复后，period 和 recovery
由 `run-due` 判定，runner 不自行补算。
