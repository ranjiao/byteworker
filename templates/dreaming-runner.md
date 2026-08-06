# Byteworker Dreaming Runner

这是用户明确启用的本地 Dreaming 定时任务。不要改变 Dreaming 设置，也不要处理未授权来源。

1. 在 byteworker skill 仓库运行一次 `bin/byteworker preflight`。
2. 从 `references/workflow-routes.json` 解析 `dreaming` workflow，并读取其完整 required 闭包。
3. 调用 `bin/byteworker dreaming run-due --kb "<KB>" --owner "<TASK_ID>"`。
4. `disabled/idle/busy` 时安静结束。
5. `leased` 时只执行返回的一个 `job/period`，遵守 Dreaming reference 对该 job 的要求；
   `job=maintenance` 时额外加载 workflow manifest 的 `features.maintenance`。
6. 保存 lease 的 `run_id`。进入 collection/analysis/consolidation/action/report/maintenance/
   recovery 阶段时调用 `dreaming heartbeat`；单阶段超过 60 秒时至少再写一次 heartbeat。
   `detail-code` 只用有限机器码，禁止放业务正文。
7. 所有来源操作只调用 `bin/byteworker` 公开命令；禁止直接 import digest/query 内部模块。
8. 成功、部分或失败路径都调用 `dreaming complete`；`partial/failed` 提供稳定错误码。已知时传
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
    `reports/<kind>/<period>.md` 归档快照，完成 lease，并按配置投递摘要。不要再分开调用
    `report render`、`complete`、`enqueue-delivery` 和 `report deliver`。
11. 任务结果必须回显 300-500 字 summary 和 HTML 绝对路径；宿主支持本地产物预览时可直接预览，
    否则返回可点击文件链接。不得调用宿主私有 HTML API。飞书失败不删除本地产物，也不得冒充
    送达。
12. 不循环领取，不创建飞书任务，不 push。除已配置的报告摘要和 maintenance 按规则
    提交的有限 doctor 决策摘要外，不发送外部消息。

启用提示已经由交互设置流程确认；runner 不重复打扰用户。机器从休眠恢复后，period 和 recovery
由 `run-due` 判定，runner 不自行补算。
