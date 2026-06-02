# byteworker · IM Inbox 摘要草案

> 用于用户要求「看最近一天 IM 里最重要的事」「从聊天里生成今日重点」「日报包含 IM」时。目标是**发现重要事项**,不是全量归档聊天。

## 1. 触发与边界

- **触发**:用户明确说「最近一天聊天 / IM / 消息 / inbox / 飞书聊天里最重要的事」,或 `daily` 请求里明确要求包含 IM。
- **默认不启用**:普通 `daily` / `weekly` 仍只跑定期摄取清单。IM 全量扫描可能量大、噪音高、权限不稳定,不能默默加入所有报告。
- **默认产物**:把最终精判后的 IM 摘要保存到知识库数据目录 `reports/im/`;不把全量聊天原文写入 `raw_data/`。只有某个 thread 被判定为应沉淀进知识库时,再按现有 `feishu_chat` 窗口规则抓取该 thread 所在小窗口并 digest。
- **本地处理**:所有临时 transcript / candidate JSON 写 `/tmp`;长期保存的只有 `reports/im/` 摘要,或被提升的标准 raw/event。业务数据不进 skill 仓库。
- **首次运行说明**:第一次运行 `bin/im-inbox-summary.sh` 时,必须先向用户说明它会扫描什么、如何本地降噪、不会默认归档全量 IM,并提醒用户补充重点项目 / 人名 / 组织 / 群名 / 指标 / 风险词。脚本会把说明写到 stderr,并在 JSON 的 `first_run_notice` 字段标记;agent 看到 `first_run_notice.shown=true` 时要把说明摘要转述给用户。
- **运行频率**:这个命令比较重,建议一天最多运行一次。脚本会记录最近一次真实运行时间;若同一天或短时间内再次运行,会在 stderr 与 JSON 的 `repeat_run_notice` 字段提醒「重复运行通常没有额外收益」。agent 看到 `repeat_run_notice.shown=true` 时要把提醒转述给用户,但不要阻断用户显式要求的重跑。

## 2. 何时调用脚本

`references/im-inbox-summary.md` 是执行规则;真正扫描 IM 时必须调用 `bin/im-inbox-summary.sh`。不要绕过脚本直接让 LLM 读取大量聊天消息。

### 必须调用 `bin/im-inbox-summary.sh`

- 用户明确要求「分析最近一天 / 今天 IM 里最重要的事」「最近一天聊天重点」「飞书消息里有什么需要关注」。
- 用户要求 `daily` / 日报「包含 IM / 聊天 / 消息 / inbox」。
- 用户要求从飞书 IM 里发现当天重要事项、待办、风险、决策,且没有指定某一个小群聊窗口作为常规 `digest chat`。
- 用户已经补充关键词 / 更新 `context.md`,并要求重新看今天或最近一天 IM。

### 不调用脚本

- 普通 `daily` / `weekly` 没有明确要求包含 IM:只跑定期摄取与知识库报告流程。
- 用户给定某个群名 / chat_id / 明确时间窗,要求「消化这个群聊」或沉淀成知识库节点:走 `references/digest-chat.md`,不是 IM Inbox 全局扫描。
- 用户只是询问知识库里已有事实,例如「X 最近在关注什么」:走 `search`,不要主动扫描 IM。
- 用户只是问这个能力怎么工作、要 review 规则、或调整 prompt / 脚本逻辑:只读本文件和脚本,不要运行扫描。

### 推荐调用方式

```bash
bin/im-inbox-summary.sh --today --kb "$KBDIR" --out /tmp/byteworker-im-inbox.json
```

若用户说「最近一天」而不是「今天」:

```bash
bin/im-inbox-summary.sh --last-hours 24 --kb "$KBDIR" --out /tmp/byteworker-im-inbox.json
```

用户给了额外关注词时,重复追加:

```bash
bin/im-inbox-summary.sh --today --kb "$KBDIR" --keyword "<项目名>" --keyword "<人名>" --out /tmp/byteworker-im-inbox.json
```

脚本输出的是候选 threads JSON。后续必须由 agent/LLM 对 `threads` 做精判,再决定 `should_include_report` 与 `should_digest_kb`;精判后的摘要写入 `reports/im/`。

## 3. 可用 CLI 能力

使用当前环境 `PATH` 中可执行的 `lark-cli`. 执行前先检查:

```bash
command -v lark-cli
lark-cli --version
```

若找不到 `lark-cli` 或认证失效,按 `lark-shared` / 本地安装说明提示用户安装、更新或重新登录;不要擅自假设安装位置。

可用入口:

- `lark-cli im +chat-list --as user --sort-type ByActiveTimeDesc --exclude-muted`
  - 列出当前用户可见群聊,按活跃度排序;用于发现最近活跃群。
- `lark-cli im +chat-messages-list --as user --chat-id <oc_xxx> --start <ISO> --end <ISO> --sort asc`
  - 拉指定群 / P2P 会话窗口消息;分页上限由脚本控制。
- `lark-cli im +messages-search --as user --start <ISO> --end <ISO> --page-all`
  - 跨聊天搜索消息;可加 `--is-at-me`、`--chat-type group|p2p`、`--sender`、`--query` 等过滤。

注意:`+messages-search` 是否允许无关键词全量时间窗搜索取决于当前 CLI / 权限表现。若 queryless 搜索失败,降级为「活跃群列表 + @我搜索 + 关键词搜索」。

## 4. 默认预算

这些是保守默认值,用户可显式调大:

| 项 | 默认 |
|----|------|
| 时间窗 | 最近 24 小时;日报中为当天 00:00..当前 |
| 活跃会话数 | 最多 30 个,排除免打扰 |
| 单会话消息数 | 最多 200 条;超出标记为 `truncated` |
| 全局消息上限 | 最多 3000 条原始消息进入本地筛选 |
| 候选 thread | 最多 300 个 |
| LLM 精判 thread | 最多 80 个 |
| 最终摘要 | 5-10 件最重要事项 |

如果超过预算,不要硬读完;应在输出中明确说明「IM 量过大,本次基于高信号候选生成摘要」。

默认运行频率:

| 项 | 默认 |
|----|------|
| 建议频率 | 一天一次 |
| 重复运行提醒 | 同一天运行,或距上次运行少于 20 小时 |
| 重复运行行为 | 只提醒,不中止 |

## 5. 处理流水线

本节描述 agent 如何使用 `bin/im-inbox-summary.sh` 的输出。采集、分页、预算控制、本地打分、降噪、thread 聚类等实现细节以脚本为准,文档不再重复维护一份伪实现。

### 5.1 准备脚本参数

先确认本次是否属于「必须调用脚本」场景。若是,按用户表达选择窗口:

- 「今天 / 日报包含 IM」→ `--today`。
- 「最近一天 / 过去 24 小时」→ `--last-hours 24`。
- 用户给了明确起止时间 → `--start <ISO8601> --end <ISO8601>`。

把知识库目录传给 `--kb "$KBDIR"`。如果用户额外给了重点项目、人名、组织、群名、指标、风险词,用重复 `--keyword <词>` 传入。输出写到 `/tmp` 文件,避免业务数据进入 skill 仓库。

### 5.2 运行脚本并读取结果

运行示例:

```bash
bin/im-inbox-summary.sh --today --kb "$KBDIR" --out /tmp/byteworker-im-inbox.json
```

脚本 stdout 若为 `output=<path>`,从该路径读取 JSON;否则从 stdout 读取 JSON。stderr 只作为用户提醒和诊断,不要当结构化数据解析。

重点读取这些字段:

- `first_run_notice`:首次运行说明。
- `repeat_run_notice`:短时间重复运行提醒。
- `stats`:扫描会话数、原始消息数、候选消息数、候选 thread 数、截断情况。
- `warnings`:CLI 权限、搜索降级、消息截断等问题。
- `threads`:本地筛出的候选 discussion threads。

### 5.3 处理提示与降级

如果 `first_run_notice.shown=true`,先向用户简要说明脚本运行逻辑与存储边界,并提醒用户后续可补充 `--keyword` 或维护 `context.md`。

如果 `repeat_run_notice.shown=true`,提醒用户 IM Inbox 是重扫描命令,建议一天一次;短时间重复运行通常收益很低。提醒即可,不要阻断用户显式要求的重跑。

如果 `warnings` 或 `stats.truncated_chats` 非空,在最终摘要或日报里说明本次基于高信号候选生成,可能存在漏召回。

如果 `threads` 为空,不要编造摘要;回复「未发现高信号 IM 事项」,同时带上扫描统计和主要 warning。

### 5.4 LLM 精判

只把 top candidate threads 交给模型,要求输出结构化 JSON:

```json
{
  "threads": [
    {
      "importance": 0,
      "relevance_to_user": 0,
      "should_include_report": false,
      "should_digest_kb": false,
      "title": "",
      "summary": "",
      "facts": [],
      "actions": [],
      "risks": [],
      "sources": [
        {
          "chat_id": "",
          "window": "YYYY-MM-DDTHH:MM:SS+08:00..YYYY-MM-DDTHH:MM:SS+08:00",
          "message_ids": []
        }
      ]
    }
  ]
}
```

判定标准:

- `should_include_report`:进入日报 / 「IM 重点」摘要。
- `should_digest_kb`:需要沉淀成 KB event / decision / 更新项目节点。只有明确决策、项目状态变化、关键风险或重要跨团队对齐才为 true。

### 5.5 输出与写入

默认写入 `reports/im/`:

- 写入前确保 `reports/im/` 目录存在;老知识库没有该目录时直接创建。
- 自然日窗口写 `reports/im/<YYYY-MM-DD>.md`;非自然日窗口写 `reports/im/<start>__<end>.md`,文件名中的 `:` 替换为 `-`。
- 复制 `templates/report-im.md` 的结构,填入最终精判摘要、待办、风险、待确认项、来源索引与扫描统计。
- 只保存最终精判后的摘要;不要把脚本 candidate JSON 全量写入知识库。candidate JSON 默认留在 `/tmp`。
- 报告是可覆盖快照:同一窗口再次生成可以覆盖原文件,但要保留用户已手动补充的 `## 手动补充 / 备注` 章节内容。
- 每条事实性摘要包括时间、群 / 会话、为什么重要、待办 / 风险、来源窗口或 message_ids。
- 汇总采集统计:扫描会话数、原始消息数、候选 thread 数、LLM 精判数、是否截断。
- 向当天 journal 追加一行,说明生成了哪个 IM 报告、参考了哪些主要 chat / thread、是否截断;在知识库数据目录本地 git 精确暂存本次报告与 journal 后创建回滚点。

若作为 `daily` 的一部分:

- 先生成 / 更新对应 `reports/im/` 报告,再把高置信项写进日报相关章节;日报「来源索引」引用该 IM 报告路径。
- 低置信但可能重要的项放「待确认」,不要写成事实。

若 `should_digest_kb=true`:

1. 对该 thread 所在 chat 重新按小窗口拉取原文,窗口通常为 thread 起止时间前后各 5 分钟。
2. 走 `references/digest-chat.md` 的标准流程:
   - `bin/resolve-users.sh --from-doc <transcript>`;
   - raw_data 使用 `feishu_chat` frontmatter;
   - 产出一个 event 或更新相关 project/person/org;
   - 更新 INDEX 与 journal。
3. 重复运行时按 `chat_id + source_window + content_hash` 避免重复入库。

## 6. 降级策略

- `+messages-search` 不支持无关键词全量搜索 → 只用活跃群 + @我 + 关键词搜索。
- `+chat-list` 权限不足 → 只扫描定期摄取清单和用户显式给出的群。
- 某会话消息过多 → 只取高信号关键词搜索命中的邻近窗口,并在摘要中标记截断。
- 人员解析失败 → 摘要中保留姓名 / open_id,不新建 person 节点。
- 附件 / 图片为核心证据但无法读取 → 标记「附件未解析」,不做强结论。

## 7. 脚本输出字段

脚本输出中的 `first_run_notice` 用于交互提示:

```json
{
  "first_run_notice": {
    "shown": true,
    "marker_path": ".../.im-inbox-summary-first-run-shown",
    "text": "..."
  }
}
```

脚本输出中的 `repeat_run_notice` 用于重复运行提醒:

```json
{
  "repeat_run_notice": {
    "shown": true,
    "recommended_frequency": "once_per_day",
    "repeat_notice_hours": 20,
    "text": "..."
  }
}
```

如果用户希望提高召回质量,优先让用户补充 `--keyword` 或更新 `context.md`,不要把大量 IM 原文直接送给模型。
