# byteworker

把你日常的飞书文档、会议、群聊,消化成一个**可对话查询的个人工作知识库**。

面向飞书重度用户(软件工程师、算法研发、PMO、运营等)—— 信息散落在文档和群里、事后再也找不回?byteworker 把它们结构化沉淀下来,需要时一句话问出来。

## 设计理念

**1. 实体图,不是文件堆**
知识库是一张实体图,6 类节点:

- 实体(持续更新):`person` 人员 · `project` 项目 · `area` 主题领域 · `org` 组织
- 记录(产生即定型):`event` 事件 · `decision` 决策

节点之间用 `links` 互链。一个项目会在多个会议、文档里被反复讨论 —— 它们全部汇聚到同一个 `project` 节点上持续生长,而不是散落各处。查「关于张三我都知道什么」= 他的 `person` 节点 + 所有链回他的事件/决策/项目。不用会漂移的「标签分类」,实体本身就是组织方式。

**2. 逻辑与数据严格分离**
**本仓库只含 agent 逻辑**(`SKILL.md` + `DESIGN.md` + `templates/` + `bin/`),不含任何业务数据。你的知识库内容存在**另一个你指定的目录**,绝不进本仓库、绝不上传 —— 因为它通常含机密工作内容。本仓库可公开,你的知识库私有,两者物理隔离。

**3. 消化,不只是存档**
摄取时 agent 会真正「消化」原始信息:抽取决策与结论、分析各参与方的立场与动机、持续更新项目的目标/进展/风险。立场分析严格基于发言证据,区分【观察】与【推断】,证据不足就说证据有限 —— 不做无依据的猜测。

**4. 可溯源、可回滚**
原始输入逐字保留,每个节点都带 `sources` 指回原文 —— 任何答案都能核对。知识库目录是它自己的本地 git 仓库,每步操作可回滚。

**5. 看板是实时视图**
工作看板每次查看都重新渲染(项目状态实时拉取、陈旧项实时扫描),永远不会过时。

## 用法

安装后,用子命令或直接自然语言:

| 子命令 | 作用 |
|--------|------|
| `/byteworker digest <飞书URL/会议/群/本地md>` | **摄取** —— 把资料消化入库 |
| `/byteworker search <问题>` | **查询** —— 问知识库,带出处与置信度 |
| `/byteworker update <节点/新进展>` | **更新** —— 某条知识有新进展 |
| `/byteworker brief` | **会前简报** —— 读飞书日历,为每个会议拉相关上下文 |
| `/byteworker dashboard` | **工作看板** —— 长期关注 / 需关注 / 今日进展 |
| `/byteworker help` | **帮助** |

也支持自然语言,如「把这个文档存进知识库」「我们关于 X 定过什么」。

## 安装

### 方式一:粘贴给 AI 助手自动安装(推荐)

整段复制下面这段,发给 Claude Code(或其他 AI 编码助手):

```
帮我安装 byteworker skill:
1. 用 git 克隆 https://github.com/ranjiao/byteworker 到 ~/byteworker
2. 建符号链接让 Claude Code 能发现它:
   ln -sfn ~/byteworker ~/.claude/skills/byteworker
   (若你不是 Claude Code,把该目录放到你发现 skill / 指令文件的位置)
3. 确认 ~/.claude/skills/byteworker/SKILL.md 存在,然后告诉我装好了
```

### 方式二:手动安装

```bash
git clone https://github.com/ranjiao/byteworker ~/byteworker
ln -sfn ~/byteworker ~/.claude/skills/byteworker
```

首次使用时,skill 会问你「知识库数据目录放在哪」—— 给一个父目录即可(目录名默认 `byteworker_kb`),它会在那里初始化结构。之后每周静默自动从 GitHub 更新。

## 知识库数据目录

你的实际知识库数据存在上面指定的独立目录(**不在本仓库**):

- `knowledge/` —— 6 类节点笔记 · `raw_data/` —— 摄取的逐字原文 · `journal/` —— 操作日志
- `INDEX.md` —— 主索引 · `dashboard.md` —— 工作看板

该目录含机密内容,仅本地、绝不外传。结构与字段设计见 [`DESIGN.md`](DESIGN.md)。

## 依赖

运行时按需调用飞书相关 skill:`lark-doc`(文档)、`lark-minutes` / `lark-vc`(会议)、`lark-im`(群聊)、`lark-calendar`(日历)、`lark-contact`(人员)。

## 文档

- [`SKILL.md`](SKILL.md) —— skill 行为定义
- [`DESIGN.md`](DESIGN.md) —— 存储结构与字段设计
- [`TODOS.md`](TODOS.md) —— 延后的功能
