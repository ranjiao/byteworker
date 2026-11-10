# byteworker · dashboard

## 查看

```bash
bin/byteworker dashboard view --kb "<KB>"
```

直接展示回执的 `data.markdown`。该内容由 Python 和 Viewer 的 `GET /api/dashboard` 共用的
只读生成器计算，包含长期关注及已保存的节点摘要、手动看板提醒和当日日志。
「需要关注」只显示手动提醒。今日进展只从当天既有 journal 渲染，并标为知识库操作记录。
生成过程不访问外部来源，不写文件、journal 或 Git，也不得创建业务事实。节点摘要引用按
`citations.md` 的规则展示已记录的出处和缺口。知识库当前没有可供看板查询的结构化未裁决冲突清单。

## 管理手动项

```bash
bin/byteworker dashboard inputs --kb "<KB>"
```

`data.watches` 和 `data.reminders` 来自知识库根目录的 `settings.md`，由日报和周报共用。
新增/删除长期关注或手动提醒时加载 `kb-mutation.md`。已有设置用 `operation: "dashboard"`
和 `replace_section` 修改对应固定章节，`base_sha256` 使用读取回执中的值。表格行的
「绑定节点」可留空，正文内的竖线写成 `\|`。长期关注尽量绑定已有知识节点。
新库首次保存使用 `replace`、空 baseline 和以下格式。

```markdown
# 知识库设置

## 长期关注

| 关注项 | 绑定节点 | 关注什么 |
| --- | --- | --- |

## 看板提醒
```

收到 committed receipt 后才宣称写入完成。记录新进展先走
对应的 update/thinking/report/digest 入口，之后即可重新查看看板。

## 兼容迁移

老库在迁移前，两个读入口只读解析旧 `dashboard.md` 的手动项。默认迁移命令仅校验，确认后加
`--apply` 写入并退役旧快照。迁移在同一个本地 Git 事务内验证长期关注、绑定和备注及手动
提醒，写入后复读确认，再删除旧文件。无法识别的旧内容、两个文件的手动项不一致或并发修改
都会中止，需先核对原内容。旧快照留在知识库 Git 历史中。

```bash
bin/byteworker kb-mutate migrate-dashboard --kb "<KB>"
bin/byteworker kb-mutate migrate-dashboard --kb "<KB>" --apply
```
