# byteworker · digest 细则 —— 定期摄取(routine digest)

> 由 `SKILL.md`「digest」一节路由到这里。**不带来源**的 `digest`、或用户说"跑定期摄取""检查周报更新"时,读本文件。

有些来源**会定期更新**(滚动周会文档、群聊等),需周期性复查增量。

- **纳入清单**:首次摄取这类来源后,**询问用户是否纳入「定期摄取」**。同意 → 给该来源的 raw frontmatter 加 `routine: weekly`(此后该源每个 raw 都带)。INDEX「定期摄取清单」表即由带 `routine` 的 raw 派生(DESIGN.md §6),无需手工维护。
- **运行**(触发:不带来源的 `digest` / 用户说"跑定期摄取""检查周报更新" / 操作前必读「到期提醒」后用户确认):
  1. 读 INDEX「定期摄取清单」,逐源处理 ——
     - 滚动周会文档:重新 `lark-doc +fetch`,把顶层最新周期按 DESIGN.md §2.1 规范化后,与该源最近 raw 的规范化 `digest_period` 比对;有新周期则按 `references/digest-doc.md` 的「滚动周会文档」规则 digest 新周期,否则跳过。
     - 群聊:`bin/pull-chat.sh --query "<群名>" --since-last`;有新消息则按 `references/digest-chat.md` digest 新窗口,否则跳过。
     - 各源增量 digest 走标准扇出:新 `event` + 实体消解**更新**已有 `project`/`person` 等节点(摄取深度沿用首次,不再重问)。
  2. **汇报**:逐源说明有无增量、digest 了哪个新周期 / 窗口、触达哪些节点。
  3. journal 追加一行「定期摄取」运行记录(审计用);并把当天日期(`YYYY-MM-DD`)原子写入数据目录的 `.last-routine-digest` —— 到期提醒据此判断(见 `SKILL.md`「操作前必读」)。INDEX「上次摄取」同步使用规范化日期 / ISO 周 / 群聊高水位。**即便本次各源都无增量也要写** —— 「复查过」与「有新增」是两回事。
- **到期提醒**:见 `SKILL.md`「操作前必读」—— 清单非空且距上次运行 ≥7 天时,skill 被使用时顺带提醒。byteworker 是 skill、不能自行定时,「到期提醒」是其可移植的 routine 实现。
