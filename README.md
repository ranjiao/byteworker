# byteworker

个人飞书工作知识库 skill —— 把飞书文档、会议妙记、本地 md 消化成结构化的**实体图知识库**,
供对话式查询与更新。面向飞书重度用户(软件工程师、算法研发、PMO、运营等)。

## 设计原则:逻辑与数据严格分离

- **本仓库只含 agent 逻辑** —— `SKILL.md` + `DESIGN.md` + `templates/`,无任何业务数据。
- **知识库数据**(`knowledge/`、`raw_data/`、`journal/`、`INDEX.md`)存在用户指定的
  独立目录,**绝不进本仓库的 git**;含机密内容,不外传。
- 数据目录在首次使用时由 skill 询问指定(默认目录名 `byteworker_kb`,路径可配置),
  记于 `.kbconfig`(已 gitignore);它有自己的独立本地 git(回滚用,永不 push)。

## 子命令

用法:`/byteworker <子命令> [参数]`(如 `/byteworker digest <飞书URL>`),或直接自然语言。

| 子命令 | 能力 | 说明 |
|--------|------|------|
| `digest` | 摄取 | 飞书文档 / 妙记 / 会议 / 群聊 / md → 消化成节点入库 |
| `search` | 查询 | "我们关于 X 定过什么" → 答案 + 出处 + 置信度 |
| `update` | 更新 | 定位节点 → 合并新信息 → 旧值进历史 |
| `brief` | 会前简报 | 读飞书日历 → 每个会议拉相关知识 |
| `dashboard` | 工作看板 | 长期关注 / 需关注 / 今日进展 |
| `help` | 帮助 | 输出用法说明 |

## 知识图模型

6 类节点 —— 实体(持续更新):`person` / `project` / `area` / `org`;
记录(产生即定型):`event` / `decision` —— 通过 frontmatter `links` 互链。
详见 [`DESIGN.md`](DESIGN.md)。

## 安装

```bash
git clone <repo-url> byteworker
ln -s "$PWD/byteworker" ~/.claude/skills/byteworker
```

首次使用时 skill 会询问你的知识库数据目录的绝对路径,并初始化目录结构。

## 依赖

运行时按需调用 `lark-doc` / `lark-minutes` / `lark-vc` / `lark-calendar` 等飞书 skill 完成摄取。

## 安全

知识库数据含机密工作内容 —— 仅存本地、绝不外传、绝不进本仓库。本仓库本身只含 agent 逻辑,不含机密。
