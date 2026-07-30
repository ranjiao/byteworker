# byteworker · digest 细则 —— 飞书文档评论

> 由 `references/digest-doc.md` 路由到这里。每次摄取或复查飞书文档时,正文与评论是两条
> 独立证据流:文档 revision 只覆盖正文,**不能证明评论没有变化**。

## 读取范围与完整性

1. 正文用 `lark-doc +fetch --api-version v2 --detail with-ids` 拉取;评论单独运行:

   ```bash
   bin/byteworker run bin/pull_doc_comments.py --url "<飞书文档 URL>" --pretty > <临时 JSON>
   ```

   临时文件只能放系统临时目录或知识库数据目录,**不得写进 skill 仓库**。脚本固定请求
   `solved_status=all`、`comment_scope=all` 和 docx relation,自动翻完评论分页;某条评论的
   `has_more=true` 时继续翻完回复分页。不能用 `lark-doc +fetch` 的成功替代这一步。
2. **已解决评论也必须读**:解决只表示评论卡片被关闭,不表示其中的指令、否决理由或历史判断
   已失去证据价值。保留 `is_solved`、`solved_time`、`solver_user_id` 及完整回复演进。
3. raw 正文先放文档原文,再以 `## 文档评论原始快照` 附加脚本输出的 `comments` canonical JSON;
   不把评论改写后的摘要冒充原文。`comment_hash` 只对 canonical `comments` 计算,不包含
   `fetched_at`,因此单纯重复拉取不会制造新版本。
4. `coverage.status=complete` 才可写 `comments_status: complete`;评论或回复分页中断一律
   视为 `partial`,不能按已经完整读取处理。接口明确成功且 `comment_count=0` 才能记录
   `comments_status: complete` + `comment_count: 0`。

## 正文锚点与滚动文档

- 局部评论优先解析 relation 中的关联对象 / block 标识,对齐
  `lark-doc +fetch --detail with-ids` 返回的正文 block。不同文档版本的 relation JSON 形态可能
  不同,保留原始 relation 后再匹配,不要假定只有一种固定字段。引用时记录 `comment_id` /
  `reply_id` 与能确认的 block id;若只有 `quote` 可做弱文本匹配,必须标“按引用片段推定位置”。
- 全文评论(`is_whole=true`)没有局部 block,按全文意见处理,不得臆造锚点。
- relation 指向嵌入 sheet / bitable / whiteboard 时,先记录 `parent_type` / `parent_token`。
  当前正文内嵌 whiteboard 已按 `references/digest-whiteboard.md` 默认摄取,直接用 token 对齐;
  外部 whiteboard、sheet / bitable 仍只有在它是重要依赖且用户同意扩展范围时才下钻。
- 滚动文档默认消化最新周期时,纳入以下评论:
  - relation block 命中该周期正文;
  - 全文评论或无可靠锚点评论,但评论 / 回复发生在本次增量窗口内;
  - 下节 P0 重点人员的评论:即使锚点不明确也保留并提示“周期归属待核”。
  其它旧周期评论留在原始评论快照中,不重复写进当前周期节点。`comment_hash` 对完整评论快照
  计算,因此旧周期评论变化也会保守地触发一次同源新版本检查;节点只写实际相关的增量。

## 人员优先级

先读 `context.md`,再把评论 / 回复里的 `user_id` 批量交给
`bin/byteworker run bin/resolve-users.sh --ids ... --format json`，解析姓名、`feishu_id` 与当前通讯录画像;
新建 / 更新 person 的规则仍按 `references/digest-doc.md`。

- **P0 必看**:
  - `context.md` 明确记录的直属上司 / 汇报对象;
  - 用户明确交代“特别关注其观点 / 意见 / 指令”的人员。
- **P1 高关注**:使用者本人、`context.md` 明确记录的上级链路 / 主管方向负责人。
- **普通**:其余作者。

P0 / P1 是**抽取与提醒优先级**,不是可信度加权:

- P0 的每条评论及别人对它的回复都要检查,不因短、已解决或位于回复链中而跳过;
- 涉及方向调整、否决条件、风险、评价标准、资源取舍、明确指令或 @使用者的内容,必须进入相应
  event / project / area / person,并在 digest 汇报中单独提醒;
- 同一人后续回复更正、撤回或改变意见时,按时间保留演进,不得只留早期意见;
- P0 / P1 的身份若只能按姓名弱匹配,标“身份待核”,不得把同名者自动合并。

## 证据语义与写入

- 评论是“某人在某时说了什么”的直接证据,**不是评论内容所声称业务事实的自动证明**。
  写成`【主张】某人认为…`、`【意图】某人要求 / 计划…`或`【观察】某人在评论中指出…`;
  只有正文、数据或其它一手来源同时支持时,才把其中客观命题写成已验证事实。
- 回复链有争议时并列各方观点和时间;评论已解决不等于争议结论已被证实。
- 评论中的明确 @本人行动项按 `references/todo.md` 产出 Todo 候选;未经用户确认仍不得写
  `todo.md`。
- 评论驱动的节点条目必须把当前 raw 加进 `sources`;引用规则见 `references/citations.md`
  的“文档评论证据”。
- 每条被节点事实使用的评论 / 回复都必须生成 provenance anchor:
  `doc:comment:<comment_id>` 或 `doc:comment:<comment_id>:reply:<reply_id>`,保存作者、原始时间、
  relation block locator 和可打开 URL / 文档 fallback;节点 `[E<n>]` 映射到该 anchor。
  只有 quote 弱匹配时 precision 不得标 `exact`。

## 评论独立幂等

- 飞书正文使用 `body_hash`,评论使用 `comment_hash`;评论 component 与正文/白板等 component
  由 `bin/digest-txn.py` 按 `byteworker-payload-v1` 计算组合 `content_hash`。
- 新式 `feishu_doc` digest key:
  `feishu_doc:<document_id>:<digest_period-or-->:<content_hash>`；`body_hash` /
  `comment_hash` / `whiteboard_hash` 作为可诊断的独立 component hash 保留。
- 正文相同、`comment_hash` 改变 → **评论增量**,写新 raw 并更新同一个主记录 / 实体节点;
  不能因 `revision_id` 或 `body_hash` 未变而 no-op。
- 正文、评论及其它实际 payload component hash 都相同 → no-op。
- 历史 raw 没有 `comments_status` / `comment_hash` 只说明“当时未记录是否检查评论”。
  第一次按新规则复查时必须实际抓评论,不能把旧 raw 当作已有空评论快照。
