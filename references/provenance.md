# byteworker · 节点出处与事实证据

> 标准 digest 必读。目标是让用户从知识节点的一条关键事实,一步回到飞书文档章节、评论 /
> 回复、妙记片段、会议、白板节点或聊天消息;无法精确定位时必须诚实降级。

## 1. 两层引用

- `[E1]`:节点内稳定证据号。写在关键事实句尾,持久映射到 `raw_id + anchor_id`。
- `[S1]`:一次回答 / 报告中的动态引用号。查询时从 `[E]` 或历史 `sources` 取证后重新编号。

不要把两者混用。一个 `[E]` 可以支持相邻多句同一事实,不同来源或不同事实应分开编号。

## 2. 抓取时保留 locator

Agent 拉取原文时同步生成 anchors,不要在摘要完成后再猜位置:

- 文档正文:`doc:block:<block_id>`;保存 block id、所在 heading、原文 URL / fragment。
- 评论:`doc:comment:<comment_id>`;回复再用
  `doc:comment:<comment_id>:reply:<reply_id>`;保存 relation block id、作者、创建时间。
- 聊天:`chat:message:<message_id>`;保存 chat/message/thread id、消息时间及飞书可生成的消息 URL。
- 妙记:`minutes:segment:<稳定片段 id 或 start_ms>`;保存片段时间范围和录屏 URL。
- 白板:`whiteboard:node:<node_id>`;保存白板 token、节点 id;若飞书不能深链则 fallback 到宿主文档。
- 网页 / 本地文件:优先稳定章节 id;否则用标题 + 行号并标 `source_only` 或 `unresolved`。

quote 只保存定位所需的短片段,不是 raw 的替代品。只有原系统稳定 locator 可证实时标 `exact`;
历史重拉并核对同版本 / 同窗口得到的定位标 `refetched`。

## 3. 节点候选与 manifest

1. 选择主记录的 `primary_source`。`event` / `decision` / `reading` 必填;持续实体只有确实存在
   一份主要文档 / 会议时才填。
2. 候选正文的关键事实句尾写 `[E1]` 等。事实包括:指标与数字、日期 / 排期、当前状态、
   决策、风险、负责人、待办、明确指令、评论 / 发言观点。
3. plan 的 `provenance` 不可省略，`anchors` 放本次 raw anchors；每个 node 必须显式给
   `evidence` 数组，新节点至少一条。evidence 逐条给 `id + anchor_id`,跨 raw 时再给 `raw_id`。
4. 不手写节点 `primary_source_url` 和末尾 `## 证据`;事务会校验 marker 集合完全一致并生成。

一个节点 body 有 `[E1]` 却没有映射,或映射有 `E2` 但正文没使用,validate 必须失败。

## 4. 历史 raw 回填

```bash
python3 bin/byteworker-cli.py provenance-backfill audit
python3 bin/byteworker-cli.py provenance-backfill plan --output "<临时目录>/provenance-plan.json"
python3 bin/byteworker-cli.py provenance-backfill validate --plan "<审核后的 plan>"
python3 bin/byteworker-cli.py provenance-backfill apply --plan "<审核后的 plan>"
```

- `audit` / `plan` 只读;plan 默认所有项 `apply:false`,不会自动迁移。
- raw 不改写。回填只新增 / 明确替换 `provenance/*.json`,以及更新被选中的节点。
- 只有一个可打开 `sources` 的节点可提议主要来源;多来源节点保持 `ambiguous`,不擅自选择。
- 离线只能可靠恢复整份来源和 raw 内已有的稳定 block id。评论 / 聊天消息缺 id 时应受控重拉;
  无法证明同版本 / 同窗口时保持 `source_only` / `unresolved`。
- `apply` 要求知识库无 remote、无 staged 变更、目标路径无未提交冲突;全程持锁、原子写入并
  创建本地 Git 回滚点。计划和候选含业务路径 / 内容,只能放系统临时目录或知识库目录。
