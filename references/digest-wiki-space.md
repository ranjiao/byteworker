# byteworker · 飞书知识库空间探索与页面选择

> 仅当用户给出 Wiki 空间首页、要求浏览知识库结构、选择子树，或恢复批量 Wiki digest 时加载。
> 普通飞书 Wiki 文档链接仍按 `feishu_doc` 处理，不加载本文件。

## 不变量

- Wiki 树只是**目录结构**，不是可直接 digest 的正文。最终每个被确认页面仍是独立
  `feishu_doc`，走普通文档 Bundle、评论/白板、依赖闸门与 digest transaction。
- 所有 Wiki API 都显式使用 `--as user`。URL/token 先经 `node-get` 解析实际
  `space_id + node_token`，不得把 URL 或 token 猜成 `space_id`。
- 空间首页的 `has_child=false` 不代表空间没有内容；全空间入口必须调用
  `node-list --space-id` 读取根节点。
- 完整树状态**没有 TTL**。默认不因“过期”自动全量重刷；只有用户明确要求重新扫描整库才刷新
  baseline。routine 只允许监控用户选定的子树。
- stdout 只显示数量、有限主题/候选预览和状态回执；近万节点的完整树只写
  `<KB>/state/wiki/`，不进入 LLM context、`raw_data/`、`knowledge/` 或 skill 仓库。
- 任一分页、权限、限流或节点读取失败时 fail closed；不以部分结果覆盖上一次完整状态。

## 交互流程

### 1. 权限预检

先问用户是否愿意处理 Wiki 读取权限；用户确认后运行：

```bash
python3 bin/byteworker-cli.py wiki auth-status
python3 bin/byteworker-cli.py wiki inspect --url "<Wiki URL>"
```

必须确认 `identity=user`、`ready=true` 和 `wiki_node_read_scope=true`。若返回
`WIKI_KEYCHAIN_ACCESS_BLOCKED`，引导用户在 Terminal.app / iTerm 执行：

```bash
security unlock-keychain "$HOME/Library/Keychains/login.keychain-db"
lark-cli config keychain-downgrade
```

第二条只在解锁后仍无法从宿主进程读取凭据时使用。不得显示、复制或记录 token。

### 2. 组织绑定确认

成功解析空间后，询问它是否是某个特定组织的官方/主要知识库，选项语义为：

- 官方或主要知识库；
- 相关但非权威；
- 不关联组织。

不得从空间标题自行推断组织。用户确认后，组织关联通过现有 `update` / 普通 digest 流程写入
对应 `org` 节点，并标明“用户确认”；树节点数、扫描时间、任务租约等易变运行状态不写组织节点。

### 3. 首次 baseline 与主题方向

只有用户明确同意探索整个空间时才运行：

```bash
python3 bin/byteworker-cli.py wiki scan \
  --kb "<KB>" --url "<Wiki 空间 URL>" --max-nodes 20000
python3 bin/byteworker-cli.py wiki topics \
  --kb "<KB>" --space-id "<space_id>" --limit 30
```

主题列表是基于标题、路径和后代数量的**结构观察**，不是对正文内容的结论。向用户展示主要方向、
文档数和覆盖范围，询问感兴趣的方向。不要把全树贴进对话。

若已存在 baseline，默认直接读 `wiki topics`；不因时间久远自动重扫。只有用户要求“刷新整库
结构”或明确接受全量 API 成本，才再次执行全空间 `scan`。

### 4. 选择子树并筛选页面

用户选择一个方向后，用其真实 `node_token` 单独扫描子树：

```bash
python3 bin/byteworker-cli.py wiki scan \
  --kb "<KB>" --url "<Wiki 空间 URL>" \
  --root-node-token "<node_token>" --max-nodes 20000
```

需要定期关注时，经用户确认创建子树 Profile；默认只比较结构，不自动全量读取页面正文：

```bash
python3 bin/byteworker-cli.py wiki profile-create \
  --kb "<KB>" --url "<Wiki 空间 URL>" \
  --root-node-token "<node_token>" \
  --routine weekly --change-detection structure_only
```

候选页筛选必须满足：标题非空、对象类型为 `doc/docx`、`document_id` 去重；需要“最近更新”条件
时显式提供带时区的 `--updated-after`，更新时间未知的页面不冒充新页面。候选列表写到系统临时
目录或 KB，不写 skill 仓库：

```bash
python3 bin/byteworker-cli.py wiki candidates \
  --kb "<KB>" --space-id "<space_id>" \
  --root-node-token "<node_token>" \
  --updated-after "<ISO8601>" --max-pages 500 \
  --out "<selection.json>"
```

向用户展示总数、有限预览、筛选条件和排除原因，确认**具体页面列表**后才可 digest。超过
`max-pages` 时要求继续缩小子树；不得静默提高上限。

### 5. 创建或恢复批量任务

页面数较多时，先估算输入 token 范围；明显较大时提醒用户成本，得到确认后创建持久任务：

```bash
python3 bin/byteworker-cli.py digest-job create \
  --kb "<KB>" --selection "<selection.json>" \
  --title "<用户可识别标题>" --batch-size 5
```

此后按需加载 `references/wiki-digest-jobs.md`。自然语言中使用任务标题与进度，不要求用户记
job ID。

## routine 子树规则

- 每个 Profile 精确对应 `feishu_wiki:<space_id>:<root_node_token>`，不同子树彼此独立。
- routine 默认 `structure_only`；它只重扫该子树并报告 added/changed/left_subtree。
  `left_subtree` 只表示离开当前目录范围，不表示删除。
- `new_pages` 或 `new_and_updated` 会增加逐节点元数据请求，只能由用户显式选择。
- 结构变化只生成有限回执，必须再次向用户确认页面列表，不自动创建 digest job。
- Wiki Profile 是探索/监控配置，不是 SourceBundle provider；不得在
  `lib/digest_txn.py` 或 `lib/kb_query.py` 增加 Wiki 私有分支。

## 冷路径要求

用户未使用 Wiki 功能时：

- 不运行 Wiki auth/API；
- 不扫描 `state/wiki/` 或 digest job；
- 不创建 `state/`；
- 不加载本文件或任务队列细则；
- 普通文档、查询、Todo、routine 的既有路径不导入 Wiki 模块。
