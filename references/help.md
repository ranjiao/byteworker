byteworker 个人知识库 —— 用法

用法:/byteworker <子命令> [参数],或直接自然语言。

digest     摄取 —— 把资料存进知识库
  /byteworker digest <飞书文档/妙记 URL | 会议 | 群 | Meego/Base 视图 | 风神看板 | 外部 blog/论文 | 本地 md>
  也可:"把这个文档存进知识库 <URL>" / "把『XX群』最近一周的讨论存进来"
       / "跑定期摄取" —— 复查清单内会定期更新的文档/群/Meego/Base/风神有无新增并消化
  → 拉取原文 → 消化成 人员/项目/主题领域/组织/事件/决策 节点 → 入库

search     查询 —— 问知识库
  /byteworker search 关于某人我知道什么 / 我们关于X定过什么 / Y项目现在怎样
  → 答案 + 论文式引用(原始文档 / 录屏 + 原文时间 + byteworker 收录时间)
    + 置信度(高 / 中 / 低-未命中)
  → 询问 Meego / Base / 风神的具体记录或报表时，从完整 raw 快照按 ID / 模糊标题确定性检索

update     更新 —— 知识有新进展
  /byteworker update 更新一下Y项目 / X决策有变动 / 这条我重新核实过了
  → 定位节点 → 合并新信息 → 旧值进「历史」→ 刷新核实日期

brief      会前简报 —— 开会前拉相关上下文
  /byteworker brief
  → 读飞书日历 → 每个会议生成相关知识简报

dashboard  工作看板 —— 看当下该关注什么
  /byteworker dashboard / 长期关注X / 看板提醒Y
  → 长期关注项(自动拉最新状态)+ 需关注事项 + 今日进展

todo       待办提醒 —— 直接说自然语言,不用记命令或编号
  "明天下午三点提醒我提交周报" / "后天关注评测结果"
  "刚才那个做完了" / "把评测结果那个延期到下周六" / "我还有什么没做?"
  → 自动解析相对时间 → 写 todo.md → 每次使用 byteworker 时检查到期 / 临期事项

自动报告   日报 / 周报 —— 安装时引导创建宿主原生本地定时任务,不用每天记命令
  默认工作日 20:30 自动日报;周一 09:30 自动生成上一完整 ISO 周周报(均可修改)
  → 每次先检查并 digest 全部已登记的定期来源 → 生成 reports/daily/ 或 reports/weekly/
  → 说“设置 / 修改 / 暂停自动报告”可管理;说“补生成 2026-05-25 日报”可人工补跑

inbox      IM摘要 —— 扫描飞书 IM 高信号消息,生成当天 / 指定窗口摘要
  /byteworker inbox / /byteworker inbox 昨天 / /byteworker inbox 2026-06-01
  → 本地筛选降噪 → 精判高信号 threads → 生成 reports/im/<YYYY-MM-DD>.md

context    全局上下文 —— 对话式维护你的工作上下文(供 agent 当「透镜」)
  "我的名字是X,飞书id是Y" / "我的当前重点改成X" / "默认提醒时间改成10点"
  → 维护 context.md 的身份 / 职责 / 重点 / 主管方向 / 约束 / 提醒偏好 / 背景

doctor     兼容诊断 —— 检查知识库是否匹配当前 skill/schema
  /byteworker doctor / "升级 skill 后检查一下知识库" / "扫描并修复知识库"
  → 默认只读列出结构、节点、raw、provenance、引用、links、INDEX 问题
    → 明确要求修复时只重建 INDEX、补双链/去重/删除自链接;语义问题不自动猜写

help       用法说明

更新 skill 不是子命令 —— 说"更新 skill""检查更新"可立即触发自动更新检查(成功检查后 7 天内静默跳过；失败按短周期退避重试；无需 GitHub 账号/SSH key)。代码确实更新后会自动运行 doctor；未完成会单独重试，不重复拉取代码。严重错误请求你决策，warning/info 只给一行摘要供你选择忽略或立即处理。固定版本环境可设置 BYTEWORKER_NO_AUTO_UPDATE=1。

上手引导   不是子命令 —— 安装完成后直接进入;摄取 / 查询演示可跳过;
           想重看说「跑一下上手引导」—— 带你走 建库 → 个性化 → 摄取 → 查询 → 自动报告

浏览       不是子命令 —— 在 byteworker skill 目录下运行 bin/browse.sh 起本地纯前端、只读
           viewer 浏览全部 md 节点(需 python3 + 本地有浏览器的环境;沙箱 / 云端 agent
           环境起不了 web 服务,browse.sh 不适用 —— 那种情况用对话查询 search 即可)

存储:知识库数据目录(用户指定,独立于本 skill,不进 git)——
      knowledge/(节点)· raw_data/(原始输入)· provenance/(精确出处)· journal/(日志)· reports/(日报/周报/IM摘要)
      · INDEX.md · dashboard.md · context.md(全局上下文)· todo.md(确认后的个人待办)
文档:DESIGN.md(存储 schema)· TODOS.md(延后功能)
安全:数据含机密内容,绝不外传、绝不进 skill 仓库的 git;Todo 仅本地,不创建飞书任务
