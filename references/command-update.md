# byteworker · update

> 仅在用户明确更新知识节点时加载。冲突只按 `conflict-policy.md` 处理，写入只走
> `kb-mutation.md`。

1. 用 `kb-query search` 定位目标节点并读取当前完整内容。
2. 用户带来外部文档、会议、聊天或其它来源时，先按标准 digest 保存 raw/provenance；不要把
   外部事实当作无来源的直接编辑。
3. 按 `conflict-policy.md` 分类：
   - 独立来源冲突：并列给用户看证据，暂停知识节点 mutation；
   - 明确 revision/supersede 或用户已裁决：保留旧值与来源，生成对应 disposition；
   - 时间较新但无修订关系：仍按冲突处理，不自动覆盖。
4. 生成完整候选节点。`sources`/`links` 去重，双向 links 完整，时间条目倒序，刷新
   `updated`；只有新输入或用户确认真正复核了当前事实才刷新 `last_verified`。
5. 将候选放在系统临时目录，构造 `byteworker-kb-mutation/v1`，其中 knowledge write 必须带
   当前 `base_sha256`、`conflict_disposition` 和必要的 `conflict_evidence`。依次运行：

   ```bash
   bin/byteworker kb-mutate validate --kb "<KB>" --plan "<plan.json>"
   bin/byteworker kb-mutate execute --kb "<KB>" --plan "<plan.json>"
   ```

6. 只有收到 `status=committed` receipt 后才能声称更新完成。INDEX、journal、精确暂存、
   commit 和失败回滚均由 mutation 工具负责，Agent 不手工执行。
