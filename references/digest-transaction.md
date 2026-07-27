# byteworker · digest 确定性写入事务

> 由 `references/digest-core.md` 路由到这里。标准 digest 的语义判断由 Agent 完成；payload
> hash、幂等检查、raw 拼装、结构校验、原子写入、INDEX、journal 与本地 Git 回滚点统一交给
> `bin/digest-txn.py`，不得再为单篇资料生成硬编码业务写入脚本。

## 职责边界

**Agent 负责:**

- 拉取正文、评论、白板、妙记或聊天原文，并把临时文件放在系统临时目录。
- 重要依赖判断、用户范围确认、冲突检测、实体消解和 Todo 候选。
- 区分【事实】/【主张】/【意图】/【观察】/【推断】。
- 按节点模板生成“完整候选文件”；更新已有节点时先把用户已有内容合并进去。

**事务脚本负责:**

- 规范化 JSON component、计算逐组件 hash 与组合 `content_hash`。
- 扫描历史 raw，返回 `new_source` / `new_version` / `noop` / `resume_failed`。
- 校验 raw id/path、节点 id/type/path、`sources`、本次新增/删除的双向 links。
- 用 `base_sha256` 阻止覆盖 Agent 读取之后发生的并发修改。
- 写锁、原子替换、失败回滚、INDEX 全量重建、journal、精确暂存和本地 commit。
- 输出 receipt；没有 `status=committed` 和 commit hash，不得向用户声称已落库。

脚本**不**理解业务内容、不决定建什么节点、不生成观点、不裁决冲突、不写 Todo，也永不 push。

## 临时 manifest

使用 `templates/digest-plan-v1.json` 作结构参考，复制到系统临时目录后填写。manifest 与候选节点
可能包含公司机密，**不得**写进 skill 仓库。

`source.components` 是本次实际摄取 payload，按原始资料中的顺序排列：

- `mode=verbatim`：逐字读取文件 bytes；正文必须用此模式。
- `mode=canonical-json`：解析 JSON、按 key 排序并去掉无意义空白后 hash；评论和白板用此模式。
- `json_pointer`：可从抓取 wrapper 中选择真正纳入 raw 的部分，如 `/comments`。
- 每个 component 的 `name` 在一次摄取内唯一且稳定；白板建议
  `whiteboard:<token>`。

飞书文档第一个 component 必须是唯一的 `kind=body`。评论完整或部分可用时必须提供唯一的
`kind=comments`；`comments_status=unavailable` 时不得伪造空评论 component。包含白板时必须
声明 `whiteboards_status=complete|partial`。

## 三段命令

### 1. preflight（写入前、无副作用）

```bash
python3 bin/digest-txn.py preflight \
  --kb "<知识库数据目录>" \
  --source "<临时 source manifest.json>"
```

处理返回值：

- `new_source`：首次来源，继续依赖判断与语义分析。
- `new_version`：同源 payload 变化；沿历史 `digest_targets` 更新已有主记录，不建重复主节点。
- `noop`：停止；不生成 plan、不写 raw/节点/journal，向用户列出现有 raw 与 targets。
- `resume_failed`：发现相同 payload 的 pending/failed raw；第一版不自动覆盖，检查历史 raw 后
  再人工恢复。

Agent 不得手算或覆盖脚本返回的 component hash、`content_hash`、`digest_key`。

### 2. validate（候选生成后、无副作用）

更新节点时必须记录读取基线：

```bash
python3 bin/digest-txn.py snapshot-node \
  --kb "<知识库数据目录>" \
  --path "knowledge/<type>/<node>.md"
```

把返回的 `base_sha256` 写入 node operation，然后：

```bash
python3 bin/digest-txn.py validate \
  --kb "<知识库数据目录>" \
  --plan "<临时 digest-plan.json>"
```

新增 link 时，反向节点必须一并提供候选更新；历史遗留的非对称/悬空 link 只 warning，不借本次
事务做全库清洗。validate 失败先修 plan，不得绕过校验手工写库。

### 3. execute（唯一标准写入口）

```bash
python3 bin/digest-txn.py execute \
  --kb "<知识库数据目录>" \
  --plan "<临时 digest-plan.json>"
```

execute 会在获取知识库写锁后重新做 preflight 和基线校验。知识库已有 staged 变更，或本次目标
路径已有未提交改动时中止；无关未暂存改动保留且不进入 commit。知识库 Git 若配置了任何
remote 也中止,避免机密数据目录进入可推送状态；脚本自身没有 push 功能。

成功 receipt 至少包含：

- `status=committed`
- `source_state`
- `raw_id` / `raw_path`
- `created` / `updated`
- `index_rebuilt=true`
- `journal`
- `commit`
- `warnings`

相同 payload 在 preflight 后被其它 Agent 先完成时，execute 返回 `status=noop`，不得重复写入。

## 候选文件规则

- Agent生成完整候选 Markdown，不使用 `replace_once` 一类脆弱文本替换 DSL。
- `create` 的目标 id/path 必须不存在；`update` 必须提供当前文件的 `base_sha256`。
- 每个候选节点 `sources` 必须包含本次 `raw_id`。
- 新节点必须包含模板要求的 status/created/updated/last_verified。
- raw 的 `digest_targets` 由事务脚本根据 plan.nodes 自动生成，Agent不得另写一套。
- journal 时刻、INDEX 重建和 Git 精确路径由脚本确定；commit message 由 plan 提供。

## 兼容旧知识库

- 旧 raw 永不改写；缺少 `payload_schema` / `payload_components` 是合法历史状态。
- preflight 优先读新 `content_hash`，并兼容旧 `body_hash` / `comment_hash` /
  `whiteboard_hash` 及历史“组件末尾补换行后直接拼接”的算法判重。
- 严格规则只约束本次新增/改变的边和文件；历史问题作为 warning。
- 不做启动时全库迁移；旧节点只有在本次真实触达时才由 Agent合并更新。
