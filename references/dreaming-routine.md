# Dreaming 定期摄取

## 先检查变化

领取 process 后用 `dreaming process digest-scan --kb "<KB>" --token "<LEASE>"`。
工具快照全部启用来源，使用已登记 Profile 并发抓取能自动重放的来源，直接核验和登记空窗口与
未变化回执。默认最多 3 路，`--max-workers 1|2|3` 可降低。遇到限流，后续波次降为串行。
它也记录单个抓取失败，成功的其它来源继续。先处理返回的所有检查，再加载语义规则。

- `status=pending` 且有 `check.status=changed` 的来源已完成预检，保留返回的 Digest 编号。
- `status=covered` 表示同一 Profile 的既有检查点已覆盖本次目标，常见于补跑旧报告；无需重复抓取。
- 其余待检查来源按 `digest-routine.md` 的对应来源条目执行捕获。文档仍须核验完整正文、
  评论（含回复、已解决评论）和白板；按需加载 `digest-comments.md` 和 `digest-whiteboard.md`。
  仅凭正文 revision 不足以确认未变化。历史 raw 兼容来源按返回的 `raw_path` 定位。
- 手动来源先调用 `digest-flow start --source-ref "<source_uid>"`，用
  `dreaming process digest-link --source-key "<source_key>" --digest-run-id "<DG>"` 关联租约和来源。
  已有 `work.digest_run_id` 时先用 `digest-flow status` 查进度和产物。捕获后用
  `digest-flow prepare --check-only --bundle-request <request.json>` 或 `--bundle <bundle.json>`。
  命令均传 `--kb`，Dreaming 命令传 `--token`，Digest 命令传 `--run-id`。先登记 noop 或
  empty_window 回执，剩余 `phase=checked` 的来源列入分析队列。
- Wiki 先按 Profile 完整扫描。需摄取变化页面时，把页面加入分析队列并为每页关联 Digest
  heartbeat。完成原有页面处理要求后再用真实 scan receipt 登记 `observed`。

手动检查在自动扫描返回后执行。独立只读采集最多 3 路，文档内组件并发计入同一上限；
依赖关系、分页顺序和限流规则按来源 adapter 执行。

## 只分析有变化的来源

用 `workflow-budget resolve --workflow digest --source-type <TYPE>` 加载该来源闭包。
只补读实际触发的 feature 和未读规则。`resume_failed` 按普通 Digest 的历史 raw 恢复闸门；
其它已检查来源用 `digest-flow analyze` 消费原 bundle，生成分析包和计划。
保持普通 Digest 的依赖、语义、冲突、候选、引用和事务规则。

## 无人值守时的处理顺序

- 依赖未获授权时，记录 `excluded_dependencies`，只摄取已授权的当前对象。继续能独立验证的结论，
  把依赖中的事实留为待核实。已登记的依赖按它自己的来源处理，维持各自的权限与覆盖。
- 冲突先回查证据和既有基线。可证明的修订或取代走既有事务规则；仍有独立冲突时保留现有知识、
  私有 Bundle 和分片，标记 `DIGEST_CONFLICT_DEFERRED`，该来源覆盖保留缺口。
- 预算超限按 `workflow-budgets.md` 去重、拆片、分批归并、复用结果或降低并发。在既定预算内继续；
  仍超限时标记 `DIGEST_BUDGET_DEFERRED`，收尾说明超限项和已尝试的处理。
- 临时失败按 adapter 的有限重试和限流规则处理。单源认证、权限或历史 raw 恢复缺少安全前提时，
  保留产物和固定原因码，处理其它来源。需要用户确认的动作只进入待办。
- 任务级授权缺失、状态损坏或写入回滚失败才停止相关任务。已写入或已核验的进度保留。

## 分片、并发与计量

- 已有分片计划时先用 `digest-parallel checkpoint --kb "<KB>" --plan <plan.json>` 查询完成项。
  每片完成后由 coordinator 立即加 `--result <worker-result.json>` 校验并保存，失败只重做缺失片。
  同一来源、内容、分片输入及 worker 规则匹配时，跨轮复用来源内的语义结果。新窗口仍须重新检查来源。
  依赖和冲突裁决使用当前 KB；跨轮只复用语义结果。
- 独立语义工作允许并发。在宿主能启动隔离 worker 且本轮预算允许时，所有来源合计最多 4 路。
  单包来源保持一个包，不同来源的包可以并行。大输入按 planner 执行。宿主容量不足时减并发，
  或逐片使用新的有界上下文。用完整 checkpoint 清单归并，覆盖不完整时保留已完成片。
  依赖范围裁决、冲突核对、候选和事务由 coordinator 按当前知识库顺序执行。
- 用 Digest 的 stage 事件记录 capture、dependency_review、semantic_analysis、reduce、
  conflict_review、candidate_generation 等阶段开始和结束。每次模型调用若有真实计数，调用
  `digest-run usage --usage-source measured`，传宿主 call id 和相应计数；若宿主同时提供调用
  耗时或距上次调用的间隔，加 `--call-duration-ms` 和 `--call-gap-ms`。只有时序也可以上报。
  只填宿主提供的值，复用分片不重新计费。需要 usage 契约时加载 `digest-observability.md`。

## 收尾

`dreaming process digest-record --input <result.json>` 接收
`byteworker-dreaming-digest-result/v1`，带原清单的 `source_key/source_type/source_uid/profile_revision`。
成功来源传 `status=committed/noop/empty_window/observed` 和真实 `receipt`。单个来源卡住时传
`status=failed/blocked` 和固定 `error_code`，保留已核验产物，继续其它独立来源。
摘要说明缺口、原因及已尝试的处理。普通缺口不发起交互，也不把子运行置为 `digest-run wait`。

最后调用 `dreaming process digest-complete --allow-partial`。成功来源各自推进有证据的覆盖，
未完成来源保留缺口。返回 partial 时，用 `dreaming complete --run-status partial` 和实际错误码
结束。普通缺口用 `DREAMING_DIGEST_INCOMPLETE`，严重错误用对应的稳定错误码。
完整覆盖仍须全部启用来源完成；后续自动领取时重新核验目标窗口，复用有效的已保存进度。
