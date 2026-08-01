# byteworker · search

> 仅在执行知识库查询时加载。公共 CLI envelope 见 `machine-protocol.md`，引用规则见
> `citations.md`。

1. 先调用：

   ```bash
   bin/byteworker kb-query search --kb "<KB>" \
     --query "<查询>" --limit 12 --graph-depth 1 --max-nodes 30
   ```

   脚本扫描 id、标题、tag、TL;DR 和正文，并在预算内扩展一跳 links。抽象表达或候选不足时，
   用一个明确近义词重跑一次；仍不足才定向检查 `INDEX.md`，不得无界扫描全库。

2. 用户询问 Meego、Base、风神的具体记录、ID、标题或状态时，必须调用
   `kb-query source-record`。默认只查每个 `source_uid` 的最新完整快照；用户明确要历史时才加
   `--history`。多条结果分数接近或跨来源同名时披露歧义，不得擅选。禁止用 `rg` / `grep`
   扫完整结构化 raw。

3. 定向读取候选节点及有限一跳邻居。对最终答案中的每条事实，从节点 `sources` 继续解析到
   raw 和原始来源。节点带 `[E<n>]` 时优先调用：

   ```bash
   bin/byteworker kb-query evidence --kb "<KB>" \
     --node "<node-id>" --markers E1,E2
   ```

4. 完整执行 `citations.md`。每个知识库事实段落或列表项就近标 `[S<n>]`；末尾给出原始出处、
   原文时间或覆盖范围、`ingested` 收录时间、版本/raw_id 和置信度。

5. 置信度：
   - 高：直接相关且 `status: current`；非 reading 节点还要求 90 天内验证。
   - 中：节点 stale、超过 90 天、仅间接命中，或关键出处/收录时间缺失。
   - 低/未命中：只允许再做一次近义词、tag/一跳邻接和近期 journal 的有限放宽；说明检索方向
     与覆盖，不把“可能没找到”写成“知识库确定没有”。

输出先给 TL;DR，再展开事实、引用和置信度。时间敏感事实来源明显旧时正文直接提示可能已过期。
