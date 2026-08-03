# byteworker · Wiki 批量 digest 任务

> 只在候选页面已由用户确认且创建了任务，或用户要求恢复/查看批量 Wiki digest 时加载。

## 状态与恢复

任务保存在 `<KB>/state/digest_jobs/WJ-*.json`，是本地持久的运行 checkpoint，不进入实体图或
skill Git。先列活动任务，再按标题向用户确认恢复哪一个：

```bash
bin/byteworker digest-job list --kb "<KB>" --active
bin/byteworker digest-job status \
  --kb "<KB>" --job-id "<job_id>" --limit 20
bin/byteworker digest-job reconcile \
  --kb "<KB>" --job-id "<job_id>"
```

`reconcile` 用已提交且 `digest_status=digested` 的 raw 修复“文档事务已提交、任务状态尚未来得及
标记”的崩溃窗口。它不修改 raw 或知识节点。

## 分批执行

每次只租约一个小批次，默认 5 页：

```bash
bin/byteworker digest-job next \
  --kb "<KB>" --job-id "<job_id>" --limit 5 \
  --lease-owner "<当前 session 稳定标识>" --lease-seconds 1800
```

对返回的每个页面先解析 `references/workflow-routes.json` 的 `wiki_resume_page`，递归展开
`extends=digest` 并读取完整公共闭包与 `digest-doc.md`；不要依赖上个 session 已读过“普通
feishu_doc 流程”。每页 worker 必须显式使用 `fork_turns="none"`，只接收页面身份、确认范围、
KB 与临时 artifact 路径；不得继承主对话，也不得由主 Agent 重复读取正文。必须以
`digest-txn execute` receipt 标记：

```bash
bin/byteworker digest-job mark \
  --kb "<KB>" --job-id "<job_id>" --document-id "<document_id>" \
  --status committed --raw-id "<raw_id>" --commit "<commit>"
```

其他状态：

- `noop`：幂等预检确认同一版本已 digest；
- `blocked_dependency`：等待用户决定重要依赖；
- `blocked_conflict`：等待冲突裁决；
- `retryable_error`：网络、临时限流等可重试错误；
- `permanent_error`：资源删除或确定无权限；
- `skipped`：用户明确跳过。

依赖或冲突经用户解决后，先把该页标为 `retryable_error`（error 说明“用户已裁决”），再由
`next` 重新领取；不要直接绕过租约写 completed 状态。

租约过期的 `in_progress` 页面可由新 session 重新领取，attempt 会递增。完成页不可倒退为失败。
任务所有页面为 `committed/noop/skipped` 时自动完成；存在依赖或冲突时进入 `waiting_user`。

用户要求取消时：

```bash
bin/byteworker digest-job cancel \
  --kb "<KB>" --job-id "<job_id>"
```

取消只把未完成页标为 `skipped`，不删除已经写入的 raw 或知识节点。

## 规模与上下文控制

- `create` 回执给出低/高 token 估算；大量页面时必须在开始前提醒用户。
- `status` 和 `next` 都有预览/批次上限，不把完整任务或页面正文塞进单轮 context。
- 主 Agent 只按批次有界等待并接收阶段状态与紧凑 receipt，不主动轮询，不收回正文、候选全文或 diff。
- 每页处理完成立即 `mark`，不要等整个批次完成才记录。
- 任务文件只保存已确认的页面身份、状态和回执定位，不保存页面正文、token 或 cookie。
