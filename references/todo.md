# byteworker · Todo 细则

> 由 `SKILL.md` 路由到这里。Todo 是知识库数据目录顶层 `todo.md` 中的用户行动状态，
> 不是飞书任务；skill 仍然不调用 `lark-task`。

## 触发与交互

以自然语言为主。用户说“记个待办”“提醒我”“明天 / 后天 / 下周六要做 X”“刚才那个做完了”
“把 X 延期到下周二”“取消刚刚的提醒”“我还有什么没做”等，都走本流程。
`/byteworker todo` 只作为兼容 / 调试入口，帮助文档不要求用户记子命令或 todo id。

Todo id (`T-YYYYMMDD-NNN`) 仅供内部去重、来源关联和脚本操作。正常交互中不要要求用户输入 id。

## 每次 skill 运行的提醒检查

完成自动更新、定位知识库目录、读取 `context.md` 后，运行：

```bash
python3 bin/byteworker-cli.py todo <知识库目录> init --template templates/todo.md
python3 bin/byteworker-cli.py todo <知识库目录> check
```

解析统一 envelope：`status=success` 时 Todo 原始结果位于 `data`；`status=error` 时按
`error.code/message/hint` 处理。底层仍由 `bin/todo.py` 执行。

`init` 返回 `created: true` 时,这是一次真实用户状态初始化:追加 journal,精确暂存 `todo.md` 与
journal 后在知识库本地 git 创建回滚点;返回 false 时不制造日志 / commit。

- `check` 只返回已到 `remind_at`、已逾期、或进入临近到期窗口的 active 项。
- 无命中时静默，不为“已检查”制造用户噪音。
- 有命中时在当前回答开头用一小段自然语言提醒；最多展开 3 项，其余报数量。
- 真正向用户展示提醒后，对每项运行 `mark-reminded`。逾期与临期项默认每天最多提醒一次。
- “我知道了”不等于完成；只有用户明确说完成 / 取消时才改状态。
- 这是每次 **byteworker 被宿主加载并运行** 时的拉取式检查。skill 本身不能在无对话时后台推送，
  也不能保证未加载 byteworker 的无关对话执行检查。

## 自然语言新增

先从用户原话区分两类时间：

- “明天下午三点提醒我提交周报” → `remind_at`，`due_at` 留空。
- “周五前提交周报” → `due_at`，未单独指定提醒时依赖临期检查。
- “周五前提交周报，周四上午提醒我” → 同时写 `due_at` 与 `remind_at`。
- 完全没说时间 → 可以创建无日期 todo，不要强迫用户补时间。

Agent 提取简短标题与时间短语，再调用确定性脚本；不要让脚本从整句业务文本猜任务边界：

```bash
python3 bin/byteworker-cli.py todo <知识库目录> add \
  --title "提交周报" \
  --remind "明天下午三点" \
  --source "direct:user"
```

脚本结合 `context.md` 的时区 / 默认时间，把自然语言时间规范化为带时区的 ISO8601。
写入后必须回显用户原表达与解析后的绝对时间，例如：
“已记录：明天下午三点提醒你提交周报（2026-07-24 15:00，Asia/Shanghai）。”
用户如立即纠正，直接 edit，不重复要求确认。

## 自然语言完成、延期、取消和查询

1. 先运行 `list --scope active` 取活跃项。
2. 优先使用当前对话里刚创建 / 刚提到的项；其次按标题语义匹配。
3. 只有一个合理候选 → agent 内部拿 id 调脚本，用户侧只回显标题。
4. 多个候选相近 → 列 2-3 个自然语言标题让用户选择，不暴露 id 作为必输格式。
5. 完成 → `status <id> done`；取消 → `status <id> cancelled`；延期 / 稍后提醒 →
   `snooze <id> <自然语言时间>`；修改标题 / DDL / 提醒时间 → `edit`。

“刚才那个”“这个”只有在当前对话指代唯一时才直接操作；否则简短追问，不能猜错后改状态。

## digest 候选

digest 仍把来源中的全部待办写进 event / report 对应章节；那是“来源说了什么”。另外结合
`context.md` 的“我的身份 / 我的职责范围 / 当前重点”识别可能属于用户的行动：

1. **高置信**：解析后的 @mention / `feishu_id` 精确命中本人，或姓名 / 别名与组织语境共同命中，
   且正文有明确动作、责任或截止时间。
2. **关注候选**：用户职责范围内出现重要风险、待确认、DDL，但责任人不明确。
3. **不候选**：明确分配给别人、已经完成 / 取消、一般性广播、仅主题相似。
4. 模型推导的建议必须标“推断”，不得冒充来源待办。

digest 汇报末尾一次性列出最多 5 项候选，包含“任务 / 为什么认为与你有关 / 时间 / 来源”，
询问“哪些加入 todo？回复 1、1/3、全部或都不加”。用户确认前不写 `todo.md`；未确认候选仍在
event / report 中保留来源，不会丢失。确认后以 event / raw / report id 作为 `source` 写 todo。

## 写入与状态

- 状态仅允许 `open` / `waiting` / `done` / `cancelled`。
- `due_at` 是外部截止时间；`remind_at` 是何时提醒，二者不可混用。
- `snoozed_until` 到期前不提醒；到期后重新参加检查。
- `todo.md` 是确认后用户行动状态的唯一真相源。event / report 不随 todo 完成状态反向改写。
- 每次修改 `todo.md` 后按 `references/write-rules.md` 追加 journal，并在知识库本地 git 精确提交
  本次 `todo.md` 与 journal 路径，永不 push。

## 时间解析规则

- “明天”= 下一自然日；“后天”= 两个自然日后。
- “本周六”= 当前 ISO 周周六；“下周六”= 下一 ISO 周周六。
- 单说“周六”= 最近一个尚未过去的周六。
- “月底”= 当月最后一天；“N 天后”= 当前日期加 N 天。
- 未说具体时刻：提醒用 `context.md` 的默认提醒时间，截止用默认截止时间。
- “下周”未给星期时按 `context.md` 约定的下周一默认时间。
- 解析不了或确有多种合理解释时才追问；不得悄悄编造时间。
