# byteworker · digest 分析预处理与冲突召回

> 由 `references/digest-core.md` 路由到这里。工具只做结构去噪、候选发现和有界召回；重要依赖、
> 事实抽取与冲突分类仍由 Agent 按 policy 判断；确定性工具不做语义裁决。

## 1. 一次预处理

SourceBundle preflight 返回 `new_source/new_version/resume_failed` 后，先开启 `analysis_prepare`，运行：

```bash
bin/byteworker digest-analysis prepare \
  --kb "<KB>" \
  --bundle "<SOURCE_BUNDLE>" \
  --out "<SYSTEM_TMP>/analysis-packet.json" \
  --run-id "<RUN_ID>"
```

`--out` 必须位于系统临时目录或 KB，禁止写进 skill 仓库。工具一次读取所有 component，输出
`byteworker-digest-analysis-packet/v1`：

- `dependency_candidates`：显式 URL/doc-id、最多 3 段附近上下文和确定性 relationship hint；这只是
  候选，不等于重要依赖。
- `outline`：正文标题。
- `sections[].text_items`：去除坐标、样式和重复结构后的正文、评论、回复与白板文本，保留 component
  和 JSON pointer；不得把整包打印进 tool output。
- `participant_ids`：批量交给 `resolve-users.sh`，不要逐人查询。
- `anchors`：已有 anchor 的紧凑索引，生成 evidence 时按 id 定点读取，不再重复扫描 Bundle。

CLI stdout 只返回 packet path/hash、cache hit 和计数。相同输入 hash + 相同输出路径复用既有 packet。
传 `--run-id` 时命令自动成对记录 `analysis_prepare`，失败也会关闭该阶段；Agent 不重复手工打点。
worker 启动和规则闭包加载不得计入 `dependency_review` 或 `conflict_review`。

## 2. 依赖与语义顺序

1. `dependency_review` 只消费 `dependency_candidates`，批量判断哪些候选满足
   `references/digest-dependencies.md`。不得再对正文运行多轮 `rg/jq`，也不得读取未授权依赖正文。
2. 用户确认依赖边界后进入 `semantic_analysis`，只消费 packet，形成事实、实体、决策、立场、
   evidence anchor，以及紧凑的冲突查询清单。
3. 评论和白板只从 `sections` 读取一次；schema 不确定是预处理器缺陷，不得由 Agent 用探测命令循环
   猜字段。确需补证时只按 packet 的 component/path 或 anchor 定点回读。

冲突查询清单必须放系统临时目录，格式为：

```json
{
  "schema_version": "byteworker-conflict-query/v1",
  "source_uid": "<current source uid>",
  "queries": [
    {"id": "fact-1", "query": "<用于召回已有事实的短查询>"}
  ]
}
```

最多 32 条 query，每条不超过 240 字符；只放需要与 KB 比较的候选事实/实体，不复制全文。

## 3. 单扫描冲突召回

开启 `conflict_review` 后只运行一次：

```bash
bin/byteworker kb-query conflict-search \
  --kb "<KB>" \
  --request "<SYSTEM_TMP>/conflict-query.json" \
  --limit-per-query 3 \
  --max-nodes 20
```

工具只扫描 KB 节点一次，先按 `source_uid → raw_id → sources/primary_source` 精确返回同源节点，
再为每条 query 返回有界候选、TL;DR 和最多两段短 snippet。Agent 依据
`references/conflict-policy.md` 分类；工具不会宣称 `no_conflict/revision/supersede`。

不要再用多轮 `rg INDEX.md knowledge` 做标准冲突召回，也不要默认读取所有候选的完整正文。只有
snippet 显示可能影响某条事实且不足以裁决时，才定点读取对应 `path`。`conflict_review` 不得包含
评论 schema 探测、白板遍历、人员消解、正文事实抽取或模板加载。
