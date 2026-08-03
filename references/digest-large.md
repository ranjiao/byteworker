# byteworker · digest 细则 —— 大输入:委派子 agent 摄取

> 由 `SKILL.md`「digest」一节路由到这里。输入大(长文档 / 滚动周报 / 大群聊窗口)时必读本文件。

长文档 / 滚动文档 / 大群聊窗口 digest 时,逐字正文会大量读进上下文、快速耗尽主对话。**这类大输入的 digest 委派给子 agent 在全新隔离上下文里做**,主对话只接收有限阶段状态与一段最终摘要。

- **判定**:输入大(长文档、滚动周报、大群聊窗口,或规模预估显示要读大量正文)→ 委派子 agent;短文档 / `reading` 文章 / 小群聊窗口 → 主 agent inline 做,不必委派(子 agent 有开销与交互往返成本)。
- **主 agent 自己留做的**(轻量 + 需与用户交互,不放进子 agent):
  1. 抓原文(`docs +fetch` 等的大输出只落 /tmp 文件,不得回显正文)、只扫标题、规模与组件清单;
  2. 按 `references/digest-dependencies.md` 做重要依赖初筛,需要时**与用户确认是否扩展本次 digest**;
  3. 规模预估,需要时**与用户确认摄取深度**;
  4. 滚动文档 / 群聊**首次是否纳入定期摄取**的询问。
- **隔离硬约束**:宿主支持子 agent / 多代理工具时，调用必须显式设置
  `fork_turns="none"`；**禁止**省略该参数或使用 `fork_turns="all"`。任务 prompt 必须自足，只写
  来源 URL/路径、`source_type`、用户确认的依赖/深度、KB 绝对路径和系统临时 artifact 路径，
  不复制主对话、旧文档正文、旧工具输出或先前 digest 结果。
- **委派给子 agent 的**(重量、无需用户交互):要求它先解析
  `references/workflow-routes.json` 的 `large_digest_worker`（递归展开 `extends=digest`），完整读取
  公共 required、对应 source type/feature、`on_error` 和本文件；然后执行读全文 → 冲突检测 →
  完整候选 → 临时 plan → `execute`。独立 `validate` 只在 execute 返回候选校验错误后用于排障。
  若宿主不支持子 agent,在主对话中分批处理,并继续遵守规模预估与交互交还规则。
- **单次语义工作包**:worker 从 SourceBundle 指向的 component 在系统临时目录生成一次
  `semantic-work-packet`，正文、canonical 评论和白板结构 JSON 各只纳入一次；白板按
  `references/digest-whiteboard.md` 只读 JSON。后续候选生成只消费该工作包，需要补证时按
  anchor 定点回读。禁止把完整正文、完整白板 JSON、完整候选或整份工作包打印进 tool output，
  也禁止为不同节点重复扫描全部原文。
- **主 agent 等待规则**:委派后主 agent 不再读取 component、生成候选、检查临时文件或向 worker
  发送进度催促；使用单次有界等待。超过 60 秒且没有阶段变化时可向用户发一行 heartbeat 后继续
  等待，**不得**用 `list_agents` / 高频 `send_message` / 文件轮询制造状态。worker 只有在
  `bundle_ready`、`candidates_ready`、`committed` 或需要用户裁决时才发送状态。
- **紧凑返回**:worker 最后只返回事务 `status`、`raw_id`、created/updated 路径、warnings、
  冲突/新依赖、commit hash；不返回正文、候选内容、白板内容或大段 diff。
- **防递归**:子 agent 收到的任务本身就是「执行 digest」,它**直接 inline 执行,不再起下一层子 agent**。
- **交互交还**:子 agent 撞到**需用户裁决的冲突**,或从全文中发现主 agent 初筛未识别的
  **重要依赖**,不静默处理、也不自行读取依赖正文 —— 把候选与原因写进返回摘要,交主 agent
  询问用户后再续。
- **主 agent 收尾**:把子 agent 摘要转告用户;`status=committed` receipt 是成功真相源。若需要
  trust-but-verify，只做一次紧凑检查：commit 是否为当前 HEAD、receipt 中节点是否在 INDEX、
  工作区是否保留原状态。不得打印 raw、provenance、节点正文、完整 diff 或再次做重复 preflight。
