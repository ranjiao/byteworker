# byteworker · digest 细则 —— 大输入有界并发

> 由 `SKILL.md`「digest」一节路由到这里。输入大(长文档 / 滚动周报 / 大群聊窗口)时必读本文件。

长文档 / 滚动文档 / 大群聊窗口 digest 时,逐字正文会大量读进上下文、快速耗尽主对话。**这类输入由
主 coordinator 按 `references/digest-concurrency.md` 把独立语义工作委派到最多 4 个全新隔离上下文，
再交一个 reducer 归并**；主对话只接收有限阶段状态和最终事务摘要。

- **判定**:先运行 `digest-parallel plan --stage semantic`；planner 返回 parallel 才并发委派，返回
  inline 就在 coordinator 处理唯一 shard，不凭主观感受增加 worker。
- **主 agent 自己留做的**(轻量 + 需与用户交互,不放进子 agent):
  1. 抓原文(`docs +fetch` 等的大输出只落 /tmp 文件,不得回显正文)、只扫标题、规模与组件清单;
  2. Bundle preflight 后运行一次 `digest-analysis prepare`，再按 planner 对依赖 shards 做初筛；
     merge 后只由 coordinator **与用户确认一次是否扩展本次 digest**;
  3. 规模预估,需要时**与用户确认摄取深度**;
  4. 滚动文档 / 群聊**首次是否纳入定期摄取**的询问。
- **隔离硬约束**:宿主支持子 agent / 多代理工具时，必须创建不继承主对话的全新上下文。任务
  prompt 必须自足，只写 worker role、shard/result 路径、input hash、对应规则闭包和系统临时 artifact
  路径；不复制主对话、旧文档正文、旧工具输出、其它 shard 或先前 digest 结果。coordinator 独占来源
  URL、KB 绝对路径和 digest `run_id`，普通 worker 不需要这些写权限坐标。
  `fork_turns` 是 Codex adapter 专有参数；`fork_turns="all"` 表示继承全部历史，在本流程中
  **禁止使用**。其他宿主使用自身的 fresh-context 能力，不得被要求传这个参数。
- **fan-out / fan-in**:dependency、semantic、conflict 分别按 plan 同时启动 shards；worker 只输出
  `byteworker-digest-parallel-result/v1` 路径和计数，coordinator 必须运行 `digest-parallel merge`。
  worker 按角色读取 `references/workflow-routes.json` 的 dependency/semantic/conflict worker 精确闭包，final
  reducer 读取专用紧凑 `digest_final_reducer` 闭包；除 manifest 声明的 worker prompt 外，不要读取完整
  `SKILL.md`、无关 reference 或预加载 templates。
  semantic merge 后由一个 reducer 去重并生成唯一 conflict query ledger，再由 coordinator 运行一次
  `conflict-search`；conflict merge 后由一个 final reducer 生成完整候选和临时 plan，只有它可调用
  `execute`。独立 `validate` 仍只用于 execute 返回候选校验错误后的排障。
- **单次语义工作包**:主流程通过 `digest-analysis prepare` 从 SourceBundle components 生成一次
  `byteworker-digest-analysis-packet/v1`；正文、canonical 评论和白板文本各只纳入一次，坐标/样式
  噪声不进入语义上下文。后续候选生成只消费该工作包，需要补证时按
  anchor 定点回读。禁止把完整正文、完整白板 JSON、完整候选或整份工作包打印进 tool output，
  也禁止为不同节点重复扫描全部原文。
- **主 agent 等待规则**:每轮 fan-out 后不读取 component、生成候选、检查临时文件或向 worker
  发送进度催促；使用单次有界等待。超过 60 秒且没有阶段变化时可向用户发一行 heartbeat 后继续
  等待，**不得**用 `list_agents` / 高频 `send_message` / 文件轮询制造状态。
- **紧凑返回**:普通 worker 只返回 result path、shard id、record count 或稳定错误码；final reducer
  只返回事务 `status`、`raw_id`、created/updated 路径、warnings、冲突/新依赖和 commit hash，不返回
  正文、候选内容、白板内容或大段 diff。
- **防递归**:worker 收到的是单个 shard 或 final reduce 任务，必须**直接执行对应 role，不再起下一层 worker**。
- **交互交还**:worker 发现 uncertain、独立来源冲突或遗漏的重要依赖时只写 result；merge 后由
  coordinator 关闭当前日志阶段并记录稳定阻塞码，再合并成一次短问题交给用户。不得自行读取依赖
  正文或分别询问用户。
- **主 agent 收尾**:把子 agent 摘要转告用户;`status=committed` receipt 是成功真相源。若需要
  trust-but-verify，只做一次紧凑检查：commit 是否为当前 HEAD、receipt 中节点是否在 INDEX、
  工作区是否保留原状态。不得打印 raw、provenance、节点正文、完整 diff 或再次做重复 preflight。
