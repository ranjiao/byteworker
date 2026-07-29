# byteworker · digest 细则 —— 定期摄取(routine digest)

> 由 `SKILL.md`「digest」一节路由到这里。**不带来源**的 `digest`、或用户说"跑定期摄取""检查周报更新"时,读本文件。

有些来源**会定期更新**(滚动周会文档、群聊、Meego / 多维表格保存视图等),需周期性复查增量。

- **纳入清单**:首次摄取这类来源后,**询问用户是否纳入「定期摄取」**。同意 → 给该来源的 raw frontmatter 加 `routine: weekly`(这是允许的 raw frontmatter 运维元数据更新;raw 正文仍不改)。此后该源每个 raw 都带 `routine`。INDEX「定期摄取清单」表即由带 `routine` 的 raw 派生(DESIGN.md §6),无需手工维护。
- **运行**(触发:不带来源的 `digest` / 用户说"跑定期摄取""检查周报更新" / 操作前必读「到期提醒」后用户确认):
  - 开始时先告知用户本次会复查 INDEX「定期摄取清单」里的多少个来源;逐源处理时用短状态说明当前来源、是否在拉取 / 比对 / digest / 跳过。来源较多或单源处理超过约 30-60 秒时,按 `SKILL.md`「长流程状态输出」发 heartbeat,不要等全部来源处理完才第一次汇报。
  1. 读 INDEX「定期摄取清单」,逐源处理 ——
     - 滚动周会文档:重新 `lark-doc +fetch --api-version v2 --detail with-ids`,并按
       `references/digest-comments.md` **独立复查评论**(含已解决和完整回复),正文有白板时按
       `references/digest-whiteboard.md` 复查。把顶层最新周期按 DESIGN.md §2.1 规范化后,将本
       周期正文、评论、白板等 component 交给 `digest-txn preflight`,与最近 raw 做兼容幂等
       比对。有新周期或任一实际 payload component 变化,都按 `references/digest-doc.md` 更新
       同一个主记录;`state=noop` 才跳过。`body_hash` / `comment_hash` /
       `whiteboard_hash` 会作为诊断字段保留,但不能只看 `revision_id` 或正文 hash。
     - 群聊:`bin/pull-chat.sh --query "<群名>" --since-last`;有新消息则按 `references/digest-chat.md` digest 新窗口,否则跳过。
     - Meego 保存视图:从最近 raw 读取
       `source_url / source_project_key / source_view_id / source_fields`,按
       `references/digest-meego.md` 重新运行 `source capture`。完整快照 hash 不变则跳过；
       不同时用 `source diff` 与上一份完整快照按工作项 ID 比对，只对
       `added / changed / left_view` 做语义复核，raw 仍保存本次完整快照。`left_view` 不等于
       删除。分页不完整、权限失败或超过范围上限时中止该源且不记为复查成功。
     - 多维表格视图:从最近 raw 读取
       `source_url / source_base_token / source_table_id / source_view_id / source_fields`,按
       `references/digest-base.md` 串行重新 capture。完整快照 hash 不变则跳过;不得用单页结果
       或记录更新时间游标替代完整快照。
     - 各源增量 digest 走标准扇出并通过 `digest-txn validate / execute` 写入。群聊/会议等时间流
       可产新 `event`；Meego / Base 保存视图更新同一 `reading`，普通记录变化只留 raw +
       provenance，达到晋升门槛才更新 `project` / `decision` / `event` / `area`。
  2. **汇报**:逐源说明有无增量、digest 了哪个新周期 / 窗口、触达哪些节点。
  3. journal 追加一行「定期摄取」运行记录(审计用);并把当天日期(`YYYY-MM-DD`)原子写入数据目录的 `.last-routine-digest` —— 到期提醒据此判断(见 `SKILL.md`「操作前必读」)。INDEX「上次摄取」同步使用规范化日期 / ISO 周 / 群聊高水位。**即便本次各源都无增量也要写** —— 「复查过」与「有新增」是两回事。
- **到期提醒**:见 `SKILL.md`「操作前必读」—— 清单非空且距上次运行 ≥7 天时,skill 被使用时顺带提醒。byteworker 是 skill、不能自行定时,「到期提醒」是其可移植的 routine 实现。
