# byteworker · digest 细则 —— 定期摄取(routine digest)

> 由 `SKILL.md`「digest」一节路由到这里。**不带来源**的 `digest`、或用户说"跑定期摄取""检查周报更新"时,读本文件。

有些来源**会定期更新**(滚动周会文档、群聊、Meego / 多维表格保存视图、风神看板、用户明确
选择的 Wiki 子树等),需周期性复查增量。

- **纳入清单**:首次摄取这类来源后,**询问用户是否纳入「定期摄取」**。Meego、Base、群聊、
  飞书文档、风神等已有 `sources/` profile 的来源，把 enabled/cadence 写进该来源自己的
  profile；尚无 profile schema 的来源才兼容给 raw frontmatter 加 `routine: weekly`。INDEX 优先从
  profiles 派生，同一 `source_uid` 有 profile 时，历史 raw 的 routine 不再生效。
- **运行**(触发:不带来源的 `digest` / 用户说"跑定期摄取""检查周报更新" / 操作前必读「到期提醒」后用户确认):
  - 开始时先告知用户本次会复查 INDEX「定期摄取清单」里的多少个来源;逐源处理时用短状态说明当前来源、是否在拉取 / 比对 / digest / 跳过。来源较多或单源处理超过约 30-60 秒时,按 `SKILL.md`「长流程状态输出」发 heartbeat,不要等全部来源处理完才第一次汇报。
  1. 读 INDEX「定期摄取清单」,逐源处理 ——
     - 滚动周会文档:优先从 `byteworker-source-profile/v2` 读取 document identity、周期、
       评论/白板策略，再重新 `lark-doc +fetch --api-version v2 --detail with-ids`,并按
       `references/digest-comments.md` **独立复查评论**(含已解决和完整回复),正文有白板时按
       `references/digest-whiteboard.md` 复查。把顶层最新周期按 DESIGN.md §2.1 规范化后,将本
       周期正文、评论、白板等 component 交给 `digest-txn preflight`,与最近 raw 做兼容幂等
       比对。有新周期或任一实际 payload component 变化,都按 `references/digest-doc.md` 更新
       同一个主记录;`state=noop` 才跳过。`body_hash` / `comment_hash` /
       `whiteboard_hash` 会作为诊断字段保留,但不能只看 `revision_id` 或正文 hash。
     - 群聊:从 KB `sources/` 加载 v2 Profile，运行
       `source capture --source-type feishu_chat --kb ... --source-uid ... --out <bundle>`；
       operation adapter 会把 `pull-chat.sh --since-last`、KB 高水位、overlap、逐字稿和
       locator 统一为 Bundle。有新消息则按 `references/digest-chat.md` digest 新窗口，否则跳过。
     - Meego 保存视图:从 KB `sources/` 加载 `byteworker-source-profile/v2`，按
       `source capture --source-type meego --kb ... --source-uid ...` 重放已确认坐标、字段与上限。
       不从最近 raw 拼接配置，也不接受同次 CLI 覆盖。完整快照 hash 不变则跳过；不同时用
       `source diff --kb ... --source-uid ...` 让 SnapshotStore 从已提交 raw 选择上一份完整
       快照并按工作项 ID 比对，只对
       `added / changed / left_view` 做语义复核，raw 仍保存本次完整快照。`left_view` 不等于
       删除。分页不完整、权限失败或超过范围上限时中止该源且不记为复查成功。
     - 多维表格视图:从 KB `sources/` 加载 v2 Profile，运行
       `source capture --source-type feishu_base --kb ... --source-uid ... --out <capture> --bundle-out <bundle>`；
       不从最近 raw 读取或拼接 token/table/view/fields。完整快照 hash 不变则跳过;不得用单页
       结果或记录更新时间游标替代完整快照。
     - 风神看板:INDEX 中每一行对应一个独立 dashboard-sheet `source_uid`。从 KB `sources/`
       加载该 profile，运行
       `source capture --source-type aeolus --kb "<知识库目录>" --source-uid "<source_uid>"`；
       不从最近 raw 还原 dashboard/sheet/report/filter，也不允许 Agent 用 CLI 临时覆盖。
       profile 缺失的旧来源先按 `references/digest-aeolus.md` 显式迁移，不能静默猜配置。
       完整快照 hash 不变则跳过；不同时按稳定 `report:<report_id>` diff，只复核 changed reports。
       授权过期、筛选解析失败、任一报表查询失败或规范化不确定时中止该源，不写部分 raw。
     - Wiki 子树:只运行用户已确认并保存的 `feishu_wiki:<space_id>:<root_node_token>`
       Profile，不刷新整空间 baseline。默认 `structure_only` 比对目录；只有 Profile 明确选择
       `new_pages/new_and_updated` 才增加节点元数据请求。用
       `wiki scan --kb ... --source-uid ...` 原样重放 Profile；发现变化先展示，不自动创建
       批量 digest 任务。细则见
       `references/digest-wiki-space.md`。
     - 各源增量 digest 走标准扇出并通过 `digest-txn validate / execute` 写入。群聊/会议等时间流
       可产新 `event`；Meego / Base / 风神保存视图更新同一 `reading`，普通记录/数字变化只留 raw +
       provenance，达到晋升门槛才更新 `project` / `decision` / `event` / `area`。
  2. **汇报**:逐源说明有无增量、digest 了哪个新周期 / 窗口、触达哪些节点。
  3. journal 追加一行「定期摄取」运行记录(审计用);并把当天日期(`YYYY-MM-DD`)原子写入数据目录的 `.last-routine-digest` —— 到期提醒据此判断(见 `SKILL.md`「操作前必读」)。INDEX「上次摄取」同步使用规范化日期 / ISO 周 / 群聊高水位。**即便本次各源都无增量也要写** —— 「复查过」与「有新增」是两回事。
- **到期提醒**:见 `SKILL.md`「操作前必读」—— 清单非空且距上次运行 ≥7 天时,skill 被使用时顺带提醒。byteworker 是 skill、不能自行定时,「到期提醒」是其可移植的 routine 实现。
