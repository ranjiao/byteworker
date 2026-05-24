# byteworker

把你日常的飞书文档、会议、群聊,消化成一个**可对话查询的个人工作知识库**。

面向飞书重度用户(软件工程师、算法研发、PMO、运营等)—— 信息散落在文档和群里、事后再也找不回?byteworker 把它们结构化沉淀下来,需要时一句话问出来。

## 设计理念

**1. 实体图,不是文件堆**
知识库是一张实体图,7 类节点:

- 实体(持续更新):`person` 人员 · `project` 项目 · `area` 主题领域 · `org` 组织
- 记录(产生即定型):`event` 事件 · `decision` 决策 · `reading` 外部读物

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
| `/byteworker digest <飞书URL/会议/群/外部文章/本地md>` | **摄取** —— 把资料消化入库 |
| `/byteworker search <问题>` | **查询** —— 问知识库,带出处与置信度 |
| `/byteworker update <节点/新进展>` | **更新** —— 某条知识有新进展 |
| `/byteworker brief` | **会前简报** —— 读飞书日历,为每个会议拉相关上下文 |
| `/byteworker dashboard` | **工作看板** —— 长期关注 / 需关注 / 今日进展 |
| `/byteworker context <增删改>` | **全局上下文** —— 对话式维护你的工作上下文(个人工作倾向、需要告诉模型的零散信息等) |
| `/byteworker help` | **帮助** |

也支持自然语言,如「把这个文档存进知识库」「我们关于 X 定过什么」。

## 浏览知识库

知识库节点是 markdown,直接读原始 md 格式不友好。byteworker 自带一个**纯前端、只读**的 viewer:

```bash
# 在 byteworker skill 目录下运行:
bin/browse.sh        # 起本地 viewer + 打开浏览器,Ctrl-C 停止(需 python3)
```

它起一个本地静态文件服务器(`python3 -m http.server`,零自定义后端):在一个临时目录里把 skill 自带的 viewer 与你的知识库数据目录挂在一起(只读),用 viewer 页面渲染 —— 左侧按 7 类列出全部节点 + 搜索框,点开渲染 md,frontmatter 与正文里的 `links` / 节点 id 都可点,沿实体图跳转。viewer 代码随 skill 分发、始终在本仓库内,你的数据目录一个字节都不写入;viewer 纯只读,编辑知识库仍走 byteworker skill。

> ⚠️ `browse.sh` 需要在**本地、有浏览器、能跑本地服务**的环境运行。如果你通过云平台 / 沙箱里的托管 agent(如托管 Codex / OpenClaw)使用本 skill,沙箱通常起不了 web 服务、也没有浏览器 —— `browse.sh` 在那种环境用不了,这是预期的、不是故障;那种情况直接用对话查询(`/byteworker search`)即可。

## 前置依赖

| 层 | 依赖 | 说明 |
|----|------|------|
| **byteworker 自身** | `git`、`jq`、`bash` | macOS:`brew install git jq`;Linux:`apt install git jq` |
| **飞书生态** | `lark-cli` + `lark-*` skills + 飞书登录 | 摄取飞书内容必需。安装参见[飞书 CLI 官方安装指南](https://open.feishu.cn/document/no_class/mcp-archive/feishu-cli-installation-guide.md):装 `lark-cli`、装 `lark-doc / minutes / vc / im / calendar / contact` 等 skill|

装好后运行 `bin/check-deps.sh` 可一键自查环境(逐项报 ✓/✗)。

## 安装

### 方式一:让 AI 助手安装(推荐)

把下面这句发给你的 AI 编码助手(Codex / Claude Code / OpenClaw / 其它):

```
按 https://raw.githubusercontent.com/ranjiao/byteworker/master/INSTALL.md 的说明,在我的环境里安装 byteworker skill;若发现之前没装好的残留,一并修复。
```

它会取来 [`INSTALL.md`](INSTALL.md) 照做 —— 自动判定宿主 agent、把 skill 装到对的位置、修复历史残留、检查依赖。

### 方式二:手动安装

```bash
# 按你实际用的 agent 改 SKILLS_DIR ——
#   Claude Code: ~/.claude/skills    Codex: ${CODEX_HOME:-$HOME/.codex}/skills    OpenClaw: ~/.openclaw/skills
SKILLS_DIR=~/.claude/skills
git clone https://github.com/ranjiao/byteworker.git "$SKILLS_DIR/byteworker"
"$SKILLS_DIR/byteworker/bin/check-deps.sh"      # 自查依赖,按提示补齐
```

把 skill **直接 clone 进 agent 的 skills 目录**(而非 clone 到别处再 symlink)—— 这样最稳,且自动更新依赖的 `git` remote 一步到位。沙箱 / 云环境、多 agent 共用、残留修复等细节见 [`INSTALL.md`](INSTALL.md)。

首次使用时,skill 会先问你要不要过一遍 **上手引导**(亲手走一遍 建库 → 摄取 → 查询,1-2 分钟,可跳过);无论走不走引导,都会让你指定一个**持久、私密**的知识库数据目录(默认名 `byteworker_kb`)。之后每周静默自动从 GitHub 更新。

## 知识库数据目录

你的实际知识库数据存在上面指定的独立目录(**不在本仓库**):

- `knowledge/` —— 7 类节点笔记 · `raw_data/` —— 摄取的逐字原文 · `journal/` —— 操作日志
- `INDEX.md` —— 主索引 · `dashboard.md` —— 工作看板

该目录含机密内容,仅本地、绝不外传;若在沙箱 / 云环境运行,务必选一个**跨会话持久**的路径,别放会被回收的临时盘。结构与字段设计见 [`DESIGN.md`](DESIGN.md)。

## 文档

- [`INSTALL.md`](INSTALL.md) —— 安装与残留修复说明(可直接交给 AI 助手执行)
- [`TUTORIAL.md`](TUTORIAL.md) —— 首次使用的上手引导剧本
- [`SKILL.md`](SKILL.md) —— skill 行为定义
- [`DESIGN.md`](DESIGN.md) —— 存储结构与字段设计
- [`TODOS.md`](TODOS.md) —— 延后的功能
