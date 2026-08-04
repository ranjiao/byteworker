# byteworker · thinking 思考节点

> 由 `SKILL.md` 的 `thinking` 路由到这里。用于保存和持续更新用户自己的认知、直觉、假设、
> 推演、方案草稿和未收敛问题。

## 定位

`thinking` 是用户当前思考的自然语言载体，不是外部资料摘要、客观事实或正式决策：

- 外部/内部资料本身的摘要仍用 `reading`；
- 已经拍板的选择仍用 `decision`；
- 稳定项目状态仍用 `project`；
- 跨主题且每次都应带入的少量工作底色仍用 `context.md`。

只有用户明确说“记录/保存/沉淀这个思考”“更新某份认知”或直接使用
`/byteworker thinking` 时才写入；普通讨论和头脑风暴不自动持久化。

## 最小结构

节点位于 `knowledge/thinkings/thinking-<slug>.md`。frontmatter 只强制：

- `id`、`title`、`type: thinking`
- `status: effective | inactive`
- `created`、`updated`

`tags`、`sources`、`links`、`last_verified` 都可选。正文只要求 `# 标题` 和非空自然语言，
不要求 TL;DR 或固定章节。

- `effective`：用户当前认可，查询和综合时作为“用户当前思考”使用。
- `inactive`：保留历史，默认不用于当前建议；只有用户明确查历史时才纳入。

“生效”仅表示对用户当前认知有效，不表示其中命题已成为客观事实。

## 创建与更新

1. 先按标题、主题和现有 links 搜索 `thinking`；同一稳定主题更新原节点，不按每轮对话新建。
2. 以自然语言重写当前认知，可以调整结构、删除已放弃观点；知识库本地 Git 保存旧版本，
   不强制在正文维护冗长演进日志。
3. 明确区分：
   - `【事实】`：来自可核验资料，尽量在 `sources` 或正文链接来源；
   - `【用户判断】`：用户直接表达的判断、偏好或经验；
   - `【推断】`：Agent 基于输入形成的推理；
   - `【建议】`：尚未成为正式决定的方案。
   标记是推荐写法，不要求把正文拆成固定字段。
4. 用户明确不再认可整份思考时改为 `inactive`；局部变化直接改正文。
5. 思考收敛为正式决定时创建/更新 `decision`，但不自动删除原 `thinking`。

## 写入与安全

`thinking` 不经过 digest transaction，也不要求创建 raw。按 `references/write-rules.md` 和
`references/kb-mutation.md`：

1. 写前读取现有节点和相关 links，避免重复主题；
2. 生成完整候选文件与 `byteworker-kb-mutation/v1` plan，operation 使用 `update`；
3. 由 `kb-mutate validate/execute` 原子维护节点、双向 links、`INDEX.md`、journal 和本地 Git
   回滚点，Agent 不直接写目标文件或运行 Git；
4. 只有 `committed` receipt 表示创建、更新或失效成功，永不 push 知识库。

查询时必须把 `thinking` 内容表述为“你的当前思考/用户判断/推断”，不能当作客观事实。
