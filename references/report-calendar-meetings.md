# byteworker · 报告周期日历会议发现

本流程是 legacy 日报 / 周报在完整 routine digest 之后、召回报告事实之前的固定步骤。它只处理
报告时间范围内，登录用户在主日历中**明确接受**的日程，并尝试把可访问的会议产物先 digest，
再交给报告召回。它不注册 routine Profile，也不扩大到即时会议或日程外资料。

## 1. 完整枚举已确认参加的日程

1. 按 `context view --intent report` 的时区计算报告范围，使用带显式时区的 ISO 8601 起止时间。
2. 只用 user 身份查询主日历：

   ```bash
   bin/byteworker lark calendar +agenda --as user \
     --start "<报告开始>" --end "<报告结束>" --format json
   ```

3. 只保留 `self_rsvp_status=accept` 的日程实例。`decline`、`tentative`、`no_reply`、字段缺失和
   已取消日程都不进入发现链路；重复日程按实例 `event_id` 去重，不用系列主 ID 代替实例。
4. 命令失败、授权 / scope 未就绪、返回截断或范围 / 分页不完整时，以
   `REPORT_CALENDAR_DISCOVERY_FAILED` 结束报告租约。零个 accepted 日程是完整成功，不是错误。

无人值守任务不得发起 OAuth、申请 scope、切换 bot 身份或把失败降级成“本周期没有会议”。

## 2. 逐日程发现直接会议产物

对 accepted 日程使用同一个 `--as user` 身份：

1. 用 `calendar +get --event-id <event_id>` 读取日程描述和直接附件，只提取其中明确出现的飞书
   文档 URL / token。
2. 用 `calendar +meeting --event-ids <逗号分隔实例 ID>`（每批最多 50 个）获取
   `meeting_id` 和用户主动绑定的 `meeting_note`。
3. 有 `meeting_id` 时用 `vc +detail --meeting-ids <ids>` 获取 `note_id` 和
   `minute_token`；有 `note_id` 时用 `note +detail --note-id <id>` 获取
   `note_doc_token`、按展示类型可直接读取的 `verbatim_doc_token` 与
   `shared_doc_tokens`。
4. 对只有 token 的文档先按运行时 schema 调用 `drive metas batch_query`（每批最多 200 个，
   `with_url=true`），取得真实标题、类型和 canonical URL；无法取得 URL 时不得猜域名或伪造
   `source_url`。
5. `meeting_note`、`note_doc_token`、可直接读取的逐字稿文档、会中共享文档，以及日程描述 /
   附件中的直接文档，都是本场会议的候选 `feishu_doc`。逐文档走完整文档 digest 路由，包括
   当前能力要求的评论 / 白板覆盖。
6. 有 `minute_token` 时用 `minutes +detail --minute-tokens <tokens> --summary --todo
   --chapter --keyword --transcript` 获取妙记产物；独立总结必须以 transcript 为主，AI 总结只作
   辅助。只 digest 文字产物，不下载音视频媒体。

纪要与妙记是相互独立的产物：两者都有时都可摄取，但同一结论在 event 中去重。不得按会议标题
搜索其它文档，不递归摄取候选文档正文里的依赖，也不主动申请妙记 / 文档访问权限。

## 3. Digest 与去重

- 每个可读取文档先生成 `feishu_doc` SourceBundle；每个有 transcript 的妙记生成
  `feishu_minutes` SourceBundle。日历只是发现和 event 元信息，不伪造单来源
  `feishu_meeting` Bundle。
- 每个来源都解析 `workflow-routes.json` 的 `digest` 闭包及对应 source type，先
  `digest-txn preflight`，候选完成后直接 `execute`；只认 `status=committed`。
- 同一 `event_id` / `meeting_id` 的产物扇入同一个 event。多个新来源按
  `digest-batch-plan/v2` 原子提交；已存在 event 时更新原 event，不创建同名副本。
- source UID、revision 和 transaction preflight 负责跨日报 / 周报及补跑去重；`noop` 不重复
  写 raw。报告周期发现是用户对这一固定范围的预授权，因此无人值守运行无需逐场再次确认；该
  例外不授权日程外依赖或新增 routine 来源。
- 已成功抓取的产物若 digest 事务失败，以 `REPORT_CALENDAR_DIGEST_FAILED` 结束报告，不允许把
  未落库内容直接塞进报告。

## 4. Best-effort 产物覆盖

日历枚举必须完整；单场会议的产物发现允许 best-effort：没有视频会议、未生成纪要 / 录制、单个
产物无查看权限或提供方暂不支持时，记录该日程的 `event_id`、已尝试的产物种类和稳定原因，继续
处理其它 accepted 日程。覆盖记录只用于报告审计，不把不可访问内容写成业务事实。

报告召回只消费已 committed 的会议 event / raw / provenance，并记录：accepted 日程数、发现产物
数、committed / noop 数和不可用原因计数。最终回显只给这些计数和阻塞码，不输出会议正文。
