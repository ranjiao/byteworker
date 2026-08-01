# byteworker · 错误处理

> 由 `SKILL.md` 路由到这里。摄取管线、写入、索引维护遇到失败时按本文件处理。

| 失败 | 处理 | 用户看到 |
|------|------|----------|
| Meego 未登录 | 中止来源读取,不写 raw；从 URL host 或用户选择得到站点,取得同意后运行 `meegle auth login --host <host>` | "Meego 使用独立 OAuth；请先连接对应站点" |
| Base 用户未登录 / 缺只读 scope | 中止来源读取,不写 raw；运行 `source auth-status`，按 `auth_action` 发起 split-flow，展示原样 URL + 二维码，用户完成后由 agent 收尾；不得自动切 bot | "需要登录飞书用户身份并补齐列出的 Base 只读权限" |
| Wiki 用户授权 / Keychain 不可读 | 中止树扫描,不覆盖状态；先确认用户愿意处理权限，再让用户在 Terminal.app / iTerm 运行 `security unlock-keychain "$HOME/Library/Keychains/login.keychain-db"`；仍受限才运行 `bin/byteworker lark config keychain-downgrade` | "当前宿主进程读不到 lark-cli 用户凭据，需要你在交互式终端解锁" |
| 风神凭据未配置 / 过期 | 中止来源读取,不写 raw；运行 `source auth-status --source-type aeolus`；更新环境变量或仓库外 `0600` 凭据文件，routine 不自动登录 | "风神读取授权未就绪,请更新 byteworker 的只读凭据" |
| Meego / Base 资源级 Permission Denied / `91403` | 中止,不写 raw；让资源所有者给当前用户共享权限,不要重复登录 | "登录正常,但当前用户无权读取这个空间/Base/表/视图,请检查共享设置" |
| 无权限 | 中止,不写 raw_data | "无权限访问该文档/会议,请检查共享设置" |
| 资源已删 / URL 失效 | 中止 | "该链接已失效或被删除" |
| 会议未结束 / 无纪要产物 | 中止,提示稍后 | "会议纪要尚未生成,请会后重试" |
| 网络 / 超时 | 重试 1 次,再失败则中止 | "拉取超时,已重试一次,请稍后再试" |
| 文档正文成功、评论无权限 / 超时 | 评论请求重试 1 次;仍失败可继续正文 digest,但 raw 写 `comments_status: unavailable` 或 `partial`、不写伪造的 `comment_hash`,并显式降低覆盖度;若用户明确要求“完整意见 / 上司意见”,则中止并请用户修复权限 | "正文已读取,但评论未完整取得;本次结果不代表已覆盖评论中的意见" |
| 评论分页 / 回复分页不完整 | 不得把截断内容记为 complete;保留可确认部分时写 `comments_status: partial`,列出缺口,不能据此断言某人没有意见 | "评论只读取到部分分页,相关人员意见覆盖不完整" |
| Meego / Base 视图分页未完成、offset 不推进或记录 ID 重复 | 中止该来源,不生成 capture、不写 raw、不更新例行复查成功时间 | "视图没有完整读取,知识库未更新;请缩小视图或稍后重试" |
| Wiki 树任一分页 / 子节点请求失败或达到 max_nodes/max_depth | 中止本次扫描,不以部分树替换上次完整状态；缩小到子树或显式调整上限 | "Wiki 目录没有完整读取，已保留上一次完整状态" |
| Wiki 候选页面超过上限 | 不读取正文、不自动提高上限；让用户选择更小子树或确认新的成本边界 | "候选页面过多，请继续缩小范围" |
| Meego / Base capture 超过默认 1000 条 | 中止并报告规模;让用户缩小保存视图或明确同意提高 `--max-items` | "视图超过默认摄取上限,需要确认新的范围" |
| 风神筛选无法映射、任一报表查询失败或 rows 不能精确规范化 | 整次中止,不生成部分快照、不写 raw；重新 inspect 检查报表/字段/口径 | "风神看板未能完整、确定地读取,知识库未更新" |
| `source bundle --request` 传了内联 JSON / 文件不存在 | 中止，不写 Bundle；按 `SOURCE_BUNDLE_REQUEST_INLINE_UNSUPPORTED` / `SOURCE_BUNDLE_REQUEST_NOT_FOUND` 的 hint 把 request JSON 写入临时文件；先运行 `source bundle-spec` 查契约 | "Bundle request 必须是临时 JSON 文件路径" |
| 文档/妙记 `source_uid` 错加 source type 前缀 | 中止，不生成 Bundle；文档改用 document_id/wiki token，妙记改用 minute token | "source_uid 重复添加了命名空间，请直接使用 provider token" |
| 输入类型无法判定 | 不写入；按 `semantic-policy.md` 给出最多 3 个候选类型、reason code 与证据，请用户选择 | "当前证据不足以稳定分类，请确认类型" |
| 写入中断 | temp-then-move 保证原子性,清理残留 `.tmp` | "写入失败,知识库未改动" |
| INDEX 文件数 ≠ 行数 | 全量重建 | "检测到索引不一致,已重建" |
| manifest / 候选节点不合法 | 中止,不写任何知识库文件;修正 plan 后重跑 validate | "digest 写入计划校验失败,知识库未改动" |
| update `base_sha256` 与当前文件不一致 | 中止,重新读取并合并最新节点,不得覆盖 | "目标节点在分析期间已变化,需要重新合并" |
| 相同 payload 已有 pending / failed raw | 不覆盖旧 raw;第一版交人工检查恢复 | "发现未完成的历史摄取,本次未重复写入" |
| 知识库已有 staged 变更 / 本次目标路径已有未提交改动 | 中止,不把别的工作卷入 digest commit | "目标路径有未提交修改,请先处理" |
| 事务写入 / INDEX / 校验失败 | 恢复事务前快照,不提交;若回滚也失败则高亮严重错误并保留现场 | "写入失败并已回滚,知识库未产生本次变更" |
| Git commit 失败 | 取消本次精确暂存并恢复事务前文件 | "本地回滚点创建失败,本次写入已撤回" |

> LLM digest 有丢事实/幻觉风险:`raw_data` 逐字保留 + 节点 `sources` 溯源,
> 任何答案都可回原文核对。digest 时不确定的内容宁可标注存疑,不臆造。
