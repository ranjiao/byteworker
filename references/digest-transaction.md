# byteworker · digest 确定性写入事务

> 由 `references/digest-core.md` 路由到这里。标准 digest 的语义判断由 Agent 完成；payload
> hash、幂等检查、raw 拼装、结构校验、原子写入、INDEX、journal 与本地 Git 回滚点统一交给
> `bin/digest-txn.py`，Agent 通过 `bin/byteworker digest-txn` 的统一机器协议调用；
> 不得再为单篇资料生成硬编码业务写入脚本。

## 职责边界

**Agent 负责:**

- 拉取正文、评论、白板、妙记或聊天原文，并把临时文件放在系统临时目录。
- 抓取时保留 block/comment/reply/message/segment 等原系统 locator,按
  `references/provenance.md` 生成 anchors。
- 重要依赖判断、用户范围确认、冲突检测、实体消解和 Todo 候选。
- 区分【事实】/【主张】/【意图】/【观察】/【推断】。
- 按节点模板生成“完整候选文件”；更新已有节点时先把用户已有内容合并进去。

**事务脚本负责:**

- 规范化 JSON component、计算逐组件 hash 与组合 `content_hash`。
- 扫描历史 raw，返回 `new_source` / `new_version` / `noop` / `resume_failed`。
- 校验 raw id/path、节点 id/type/path、`sources`、本次新增/删除的双向 links。
- 强制每个标准 digest 提供 provenance；校验 `primary_source`、正文 `[E]` 与 evidence 映射,
  并物化 provenance sidecar 和节点证据表。
- 用 `base_sha256` 阻止覆盖 Agent 读取之后发生的并发修改。
- update 默认禁止丢失既有来源、证据标记和正文语义；确需删除时必须给对应 removal 对象和理由。
- 写锁、原子替换、失败回滚、INDEX 全量重建、journal、精确暂存和本地 commit。
- 输出 receipt；没有 `status=committed` 和 commit hash，不得向用户声称已落库。

脚本**不**理解业务内容、不决定建什么节点、不生成观点、不裁决冲突、不写 Todo，也永不 push。

## 临时 SourceBundle 与 manifest

新增单来源使用 `templates/source-bundle-v2.json` +
`templates/digest-plan-v2.json`。来源 adapter 先在系统临时目录生成
`byteworker-source-bundle/v2`，Agent 的 `digest-plan/v2` 只引用这个 bundle；不得复制
`plan.source` 或 `plan.provenance.anchors`。两个以上必须原子落库、或一个节点同时综合多份
原文时使用 `templates/digest-batch-plan-v2.json`，每个 `inputs[]` 只引用各自
`source_bundle`。`digest-batch-plan/v1` 只兼容历史调用。

`digest-plan/v1` 保留给已有调用方和未迁移来源，不是新增 adapter 的目标格式。所有 bundle、
manifest、候选节点和 component 文件都可能包含公司机密，**不得写进 skill 仓库**。

`SourceBundle.components` 是本次实际摄取 payload，按原始资料中的顺序排列：

- `mode=verbatim`：逐字读取文件 bytes；正文必须用此模式。若同时提供 `json_pointer`，指针
  必须定位到 JSON 字符串，事务把该字符串 UTF-8 bytes 作为逐字正文，不保留 wrapper。
- `mode=canonical-json`：解析 JSON、按 key 排序并去掉无意义空白后 hash；评论和白板用此模式。
- `json_pointer`：可从抓取 wrapper 中选择真正纳入 raw 的部分，如
  `/data/document/content` 或 `/comments`。
- 每个 component 的 `name` 在一次摄取内唯一且稳定；白板建议
  `whiteboard:<token>`。

bundle 还必须显式包含：

- `identity`:稳定 `source_type/source_uid/source_url/title/revision`；
- `coverage`:总覆盖状态和逐 component 覆盖状态，禁止把未读取内容宣称为 complete；
- `anchors`:抓取时保留的精确或 source-only locator；
- `provider_metadata`:只放可序列化、非凭据的 provider 事实；
- 可选 `record_index/snapshot_hash/payload_hash`。

飞书文档第一个 component 必须是唯一的 `kind=body`。评论完整或部分可用时必须提供唯一的
`kind=comments`；`comments_status=unavailable` 时不得伪造空评论 component。包含白板时必须
声明 `whiteboards_status=complete|partial`。

v2 plan 顶层 `provenance` 只允许本次 raw 的 `enrichment`；anchors 自动从 bundle 注入。
每个 node operation 必须
显式包含 `evidence[]`，主记录还必须包含 `primary_source`。新节点至少一条 evidence。
`evidence[].id` 必须与候选正文的 `[E<n>]` 一一对应,
`raw_id` 省略时默认本次 raw,`anchor_id` 必须能在本次或既有 sidecar 中解析。

### 批量 plan

`digest-batch-plan/v2` 顶层使用 `inputs[] + nodes[]`。每个 input 都有独立
`source_bundle/raw/provenance`，其中 provenance 只写 `enrichment`；source identity、
components 和 anchors 全部从对应 Bundle 注入，禁止在 plan 复制。每个 node 用
`source_raw_ids` 声明实际依赖的本批 raw，多来源 evidence 必须显式写 `raw_id`。

batch 采用一个短时锁、一次 INDEX 重建、一条 journal 和一个 commit；任一输入已是
`noop/resume_failed` 时整批拒绝，Agent 重新排除已完成项后再规划，不做隐式部分提交。
`preflight` 仍对每个 Bundle 单独运行；确认都需摄取后再组 batch plan，随后执行
`execute`；其内部完成计划校验和锁内复检，独立 `validate` 只在失败诊断时调用。v1 的
`inputs[].source` 仅为兼容入口，新流程不得继续使用。

## 两段标准命令与可选诊断

### 1. preflight（写入前、无副作用）

```bash
bin/byteworker digest-txn preflight \
  --kb "<知识库数据目录>" \
  --source "<临时 source-bundle-v2.json>"
```

处理返回值：

- `new_source`：首次来源，继续依赖判断与语义分析。
- `new_version`：同源 payload 变化；沿历史 `digest_targets` 更新已有主记录，不建重复主节点。
- `noop`：停止；不生成 plan、不写 raw/节点/journal，向用户列出现有 raw 与 targets。
- `resume_failed`：发现相同 payload 的 pending/failed raw；第一版不自动覆盖，检查历史 raw 后
  再人工恢复。

Agent 不得手算或覆盖脚本返回的 component hash、`content_hash`、`digest_key`。

### 可选：validate（只在候选排障时使用、无副作用）

更新节点时必须记录读取基线：

```bash
bin/byteworker digest-txn snapshot-node \
  --kb "<知识库数据目录>" \
  --path "knowledge/<type>/<node>.md"
```

把返回的 `base_sha256` 写入 node operation。标准路径随后直接运行 execute；只有 execute 返回
候选 schema、link、baseline 或 evidence 校验错误，需要更完整诊断时才单独运行：

```bash
bin/byteworker digest-txn validate \
  --kb "<知识库数据目录>" \
  --plan "<临时 digest-plan.json>"
```

新增 link 时，反向节点必须一并提供候选更新；历史遗留的非对称/悬空 link 只 warning，不借本次
事务做全库清洗。validate 失败先修 plan，不得绕过校验手工写库。不要在 validate 成功后把完整
report、候选或 diff 再打印给模型；execute 会重复完成全部安全校验。

### 2. execute（候选完成后的唯一标准写入口）

```bash
bin/byteworker digest-txn execute \
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

成功 receipt 已是写入真相源。收尾默认只消费 receipt；确需额外核验时，把 HEAD、INDEX 命中和
工作区状态合并成一次紧凑检查。禁止输出完整 raw、provenance、候选节点、节点正文、完整 diff，
也禁止为“确认成功”再跑一次 preflight。

## 候选文件规则

- Agent生成完整候选 Markdown，不使用 `replace_once` 一类脆弱文本替换 DSL。
- `create` 的目标 id/path 必须不存在；`update` 必须提供当前文件的 `base_sha256`。
- 每个候选节点 `sources` 必须包含本次 `raw_id`。
- 所有节点必须显式设置 `evidence`;新节点至少一条。主记录节点必须设置 `primary_source`;
  关键事实正文必须写 `[E<n>]`,并提供完整 evidence 映射。
- update 必须保留已有 `sources`、`[E<n>]` 与实质正文。确有纠错/合并需要时，分别设置
  `source_removal` / `evidence_removal` / `content_removal` 为
  `{"allow": true, "reason": "..."}`；理由会留在临时 plan，不写入业务节点。
- Agent 不在候选中手写 `primary_source_url` 或 `## 证据`;事务从 raw / anchor 确定性生成。
- 新节点必须包含模板要求的 status/created/updated/last_verified。
- raw 的 `digest_targets` 由事务脚本根据 plan.nodes 自动生成，Agent不得另写一套。
- journal 时刻、INDEX 重建和 Git 精确路径由脚本确定；commit message 由 plan 提供。

## 兼容旧知识库

- 旧 raw 永不改写；缺少 `payload_schema` / `payload_components` 是合法历史状态。
- preflight 优先读新 `content_hash`，并兼容旧 `body_hash` / `comment_hash` /
  `whiteboard_hash` 及历史“组件末尾补换行后直接拼接”的算法判重。
- 严格规则只约束本次新增/改变的边和文件；历史问题作为 warning。
- 不做启动时全库迁移；旧节点只有在本次真实触达时才由 Agent合并更新。
- 历史批量补出处通过机器协议使用 `bin/provenance-backfill.py audit|plan|validate|apply`,不走 digest
  幂等入口。自动生成的 plan 全部 `apply:false`;只有 Agent / 用户审核后才允许 apply。
