byteworker 个人知识库 —— 用法

用法:/byteworker <子命令> [参数],或直接自然语言。

digest     摄取 —— 把资料存进知识库
  /byteworker digest <飞书文档/妙记 URL | 会议 | 群 | 外部 blog/论文 | 本地 md>
  也可:"把这个文档存进知识库 <URL>" / "把『XX群』最近一周的讨论存进来"
       / "跑定期摄取" —— 复查清单内会定期更新的文档/群有无新增并消化
  → 拉取原文 → 消化成 人员/项目/主题领域/组织/事件/决策 节点 → 入库

search     查询 —— 问知识库
  /byteworker search 关于张三我知道什么 / 我们关于X定过什么 / Y项目现在怎样
  → 答案 + 出处 + 置信度(高 / 中 / 低-未命中)

update     更新 —— 知识有新进展
  /byteworker update 更新一下Y项目 / X决策有变动 / 这条我重新核实过了
  → 定位节点 → 合并新信息 → 旧值进「历史」→ 刷新核实日期

brief      会前简报 —— 开会前拉相关上下文
  /byteworker brief
  → 读飞书日历 → 每个会议生成相关知识简报

dashboard  工作看板 —— 看当下该关注什么
  /byteworker dashboard / 长期关注X / 提醒我关注Y
  → 长期关注项(自动拉最新状态)+ 需关注事项 + 今日进展

daily      日报 —— 自动跑定期摄取,总结当天重要事项
  /byteworker daily / /byteworker daily 2026-05-25
  → 复查定期来源 → 消化新增 → 生成 reports/daily/<YYYY-MM-DD>.md

weekly     周报 —— 自动跑定期摄取,总结本周重要事项
  /byteworker weekly / /byteworker weekly 上周
  → 复查定期来源 → 消化新增 → 生成 reports/weekly/<YYYY>-W<WW>.md

context    全局上下文 —— 对话式维护你的工作上下文(供 agent 当「透镜」)
  /byteworker context 我的当前重点改成X / 主管说… / 看一下我的 context
  → 增删改 context.md 的 我的当前重点 / 主管方向 / 当前约束 / 背景信息

help       用法说明

更新 skill 不是子命令 —— 说"更新 skill""检查更新"可立即触发自动更新检查(默认每周静默自动检查一次,从 GitHub 拉取最新 skill 内容;无需 GitHub 账号/SSH key)。

上手引导   不是子命令 —— 首次使用时自动询问是否走一遍(可跳过);
           想重看说「跑一下上手引导」—— 带你走 建库 → 摄取 → 查询

浏览       不是子命令 —— 在 byteworker skill 目录下运行 bin/browse.sh 起本地纯前端、只读
           viewer 浏览全部 md 节点(需 python3 + 本地有浏览器的环境;沙箱 / 云端 agent
           环境起不了 web 服务,browse.sh 不适用 —— 那种情况用对话查询 search 即可)

存储:知识库数据目录(用户指定,独立于本 skill,不进 git)——
      knowledge/(节点)· raw_data/(原始输入)· journal/(日志)· reports/(日报/周报)
      · INDEX.md · dashboard.md · context.md(全局上下文)
文档:DESIGN.md(存储 schema)· TODOS.md(延后功能)
安全:数据含机密内容,绝不外传、绝不进 skill 仓库的 git
