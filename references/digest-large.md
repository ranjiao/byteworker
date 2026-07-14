# byteworker · digest 细则 —— 大输入:委派子 agent 摄取

> 由 `SKILL.md`「digest」一节路由到这里。输入大(长文档 / 滚动周报 / 大群聊窗口)时必读本文件。

长文档 / 滚动文档 / 大群聊窗口 digest 时,逐字正文会大量读进上下文、快速耗尽主对话。**这类大输入的 digest 委派给子 agent 在隔离上下文里做**,主对话只接收一段摘要。

- **判定**:输入大(长文档、滚动周报、大群聊窗口,或规模预估显示要读大量正文)→ 委派子 agent;短文档 / `reading` 文章 / 小群聊窗口 → 主 agent inline 做,不必委派(子 agent 有开销与交互往返成本)。
- **主 agent 自己留做的**(轻量 + 需与用户交互,不放进子 agent):
  1. 抓原文(`docs +fetch` 等的大输出会落 /tmp 文件,不占上下文)、扫标题结构;
  2. 按 `references/digest-dependencies.md` 做重要依赖初筛,需要时**与用户确认是否扩展本次 digest**;
  3. 规模预估,需要时**与用户确认摄取深度**;
  4. 滚动文档 / 群聊**首次是否纳入定期摄取**的询问。
- **委派给子 agent 的**(重量、无需用户交互):若宿主支持子 agent / 多代理工具,起一个具备 shell、文件读取、文件编辑能力的子 agent。委派前向用户说明「已进入大输入处理,会在隔离上下文里消化,这边持续同步阶段状态」;子 agent 运行期间主 agent 按 `SKILL.md`「长流程状态输出」发 heartbeat,不要长时间沉默。任务 prompt 必须**自足**(子 agent 无本对话记忆)—— 写明:来源 URL/路径与 `source_type`、**用户已确认的依赖清单与摄取深度**、知识库数据目录绝对路径,并要求它**先读** byteworker `SKILL.md`(digest 一节)+ `references/digest-core.md` + `references/digest-dependencies.md` + 对应来源细则 + `DESIGN.md`,再按规范执行:读全文 → 冲突检测 → 扇出写节点 → 更新 INDEX/journal → git 提交回滚点。子 agent 最后**只返回摘要**:新建 / 更新了哪些节点、raw_id、有无冲突、是否发现新的重要依赖、commit hash。若宿主不支持子 agent,在主对话中分批处理,并继续遵守规模预估与交互交还规则。
- **防递归**:子 agent 收到的任务本身就是「执行 digest」,它**直接 inline 执行,不再起下一层子 agent**。
- **交互交还**:子 agent 撞到**需用户裁决的冲突**,或从全文中发现主 agent 初筛未识别的
  **重要依赖**,不静默处理、也不自行读取依赖正文 —— 把候选与原因写进返回摘要,交主 agent
  询问用户后再续。
- **主 agent 收尾**:把子 agent 摘要转告用户;**trust-but-verify** —— 扫一眼 `git log` / INDEX 节点数,确认子 agent 确实写入,再汇报。
