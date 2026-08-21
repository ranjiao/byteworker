# byteworker · digest 有界并发

> 由 `references/digest-core.md` 和 `references/digest-large.md` 路由到这里。并发只缩短只读抓取与
> 可独立语义判断的墙钟时间；范围确认、跨分片归并、用户冲突裁决和事务写入始终只有一个 coordinator。

## 1. 不变量

- 最大并发度为 4；飞书正文/评论/白板默认 3，出现限流时退避并降低到 1。不得无限重试。
- coordinator 创建并持有唯一 `run_id`，只在整个阶段外层记录一次 started/completed/failed；worker
  不写 digest-run 日志。完成 metrics 写实际 `worker_count` / `shard_count` / 重试与输入输出计数。
- capture、shard、worker result、reduce packet 只能在系统临时目录或 KB 私密 state，权限 `0600`，
  不得进入 skill、raw、provenance 或 Git transaction。
- worker 只读自己的 shard 和明确的规则闭包，不读取其它 shard、完整输入、KB 全库或依赖正文，不向
  用户提问，也不写 KB。任何 worker 失败都视为该阶段 coverage 不完整，不用部分结果继续。
- 所有 fan-out 后必须 fan-in 到一个 reducer；重复、跨章节关系、独立来源冲突和用户闸门由 reducer
  统一处理。最终仍只调用一次 `digest-txn execute`。

## 2. 来源抓取

飞书文档先把正文和评论作为两个独立只读 job 一起运行；正文完成并发现内嵌白板 token 后，再把每个
白板组成第二批 job。会议簇经用户确认组成物件后，可将妙记和各文档的只读抓取并发执行。请求格式：

```json
{
  "schema_version": "byteworker-digest-capture-plan/v1",
  "max_workers": 3,
  "jobs": [
    {
      "id": "body",
      "runner": "lark",
      "args": ["docs", "+fetch", "--doc", "<URL>", "--detail", "with-ids", "--format", "json"],
      "output": "<SYSTEM_TMP>/body.json",
      "max_attempts": 2
    },
    {
      "id": "comments",
      "runner": "comments",
      "args": ["--url", "<URL>", "--jobs", "3"],
      "output": "<SYSTEM_TMP>/comments.json",
      "max_attempts": 2
    }
  ]
}
```

执行时只返回 job id、状态、尝试次数、耗时、字节数和输出路径，不回显正文：

```bash
bin/byteworker digest-capture execute --request "<SYSTEM_TMP>/capture-plan.json"
```

`runner=lark` 的 args 是 `bin/byteworker lark` 后的真实参数；先按对应 lark skill / `--help` 确认，
不要照抄过时示例。`runner=comments` 只允许 `pull_doc_comments.py`。正文发现白板后使用
`whiteboard +export --output-type raw --whiteboard-token ... --format json` 建第二批 plan。评论列表和每条
回复链内部的 page token 仍串行，但同一页中需要展开的独立回复链最多 3 路并发。

Base、群聊、评论列表等依赖顺序 page token / offset 的分页严禁并发。成功 receipt 也不等于 coverage
完整；仍按来源 adapter 校验全部 component 后才能构造 Bundle。

## 3. 人员解析

analysis packet 的 `participant_ids` 一次去重后调用：

```bash
bin/byteworker run bin/resolve-users.sh --ids "<CSV>" --format json --jobs 4
```

脚本并发执行独立通讯录只读请求，按排序后的 open_id 确定性合并；单个人的 search → fallback get
仍保持串行。解析失败继续按 person policy 标记待解析，不通过提高并发掩盖权限或身份问题。

## 4. 分片计划

三个语义阶段都先让确定性 planner 决定 inline 或 parallel：

```bash
bin/byteworker digest-parallel plan \
  --stage dependency --input "<ANALYSIS_PACKET>" --out-dir "<SYSTEM_TMP>/dependency"
bin/byteworker digest-parallel plan \
  --stage semantic --input "<ANALYSIS_PACKET>" --out-dir "<SYSTEM_TMP>/semantic"
bin/byteworker digest-parallel plan \
  --stage conflict --input "<CONFLICT_CANDIDATES>" --out-dir "<SYSTEM_TMP>/conflict"
```

固定阈值：dependency 候选 `<12` inline；semantic 同时低于 `500 text_items` 且 packet `<1 MiB`
inline；conflict query `<8` 且候选总数 `<=20` inline。超过阈值时 planner 在最多 4 个 worker 间按
文本/候选重量平衡分片。Agent 不自行改阈值或增加 worker。

每个 worker 只写一个结果文件：

```json
{
  "schema_version": "byteworker-digest-parallel-result/v1",
  "stage": "dependency|semantic|conflict",
  "input_hash": "<PLAN INPUT HASH>",
  "shard_id": "<SHARD ID>",
  "records": []
}
```

- dependency record：逐候选返回 `candidate_id`、`disposition=important|not_important|uncertain` 和大写
  `reason_code`；不得读取依赖正文。merge 后 coordinator 统一去重并最多询问用户一次。
- semantic record：返回稳定 `record_id`、`record_type`、`dedupe_key`、至少一个属于 shard 的
  `source_refs[{component,path}]` 和 `payload`。类型只允许 fact/entity/decision/stakeholder_position/
  evidence/conflict_query/todo_candidate/warning。worker 不生成节点文件、不做跨 shard 覆盖判断。
- conflict record：逐 query 返回 `query_id`、`disposition=no_conflict|revision|supersede|
  independent_conflict|uncertain` 和只来自本 shard 的 `candidate_ids`。worker 不能输出
  `user_confirmed`，独立来源冲突必须交回 coordinator。

所有结果齐备后执行：

```bash
bin/byteworker digest-parallel merge \
  --plan "<STAGE_PLAN>" --result "<RESULT_1>" --result "<RESULT_2>" \
  --out "<SYSTEM_TMP>/<STAGE>-reduce.json"
```

merge 校验 input hash、完整 shard coverage、逐项覆盖和 source/candidate 边界；semantic 只标出重复
`dedupe_key`，不冒充语义去重。单一 reducer 消费 reduce packet，解决重复与跨分片关系，然后生成
一次依赖询问、一个冲突 query ledger 或一份最终 DigestPlan。

## 5. 大输入编排

宿主支持 fresh-context worker 时，coordinator 对 planner 返回的 parallel shards 同时启动最多 4 个
不继承主对话的 worker；各角色分别读取 `workflow-routes.json` 的 `digest_dependency_worker`、
`digest_semantic_worker`、`digest_conflict_worker`，final reducer 读取 `digest_final_reducer`；inline
则在当前 coordinator 处理唯一 shard。语义 merge 后先由单一 reducer
生成不超过 32 条 conflict query，再运行一次 `kb-query conflict-search`；不得让每个 semantic worker
各扫一遍 KB。conflict 条件并发 merge 后再由同一个 final reducer 执行标题消歧、实体合并、候选生成
和一次 transaction。

宿主不支持并行 worker 时按 shard 顺序处理，仍使用相同 plan/result/merge coverage 契约。不要为了
并发让 worker 递归创建 worker，也不要在等待期间轮询临时文件或把正文传回主对话。
