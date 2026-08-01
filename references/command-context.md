# byteworker · context

> 仅在查看或由用户明确要求维护全局工作上下文时加载。

其它 digest/search/report 流程只能通过 `context view --intent ...` 读取投影，不得修改。

查看：

```bash
bin/byteworker context view --kb "<KB>" --intent "<intent>"
```

用户明确增、改、删时：

1. 读取 `context.md`，确认要修改的固定章节。拿不准章节就询问用户。
2. “我的身份”保留固定表格；其它章节使用简短条目。姓名、别名、feishu_id 是本人匹配依据，
   open_id 不作长期主键。
3. 将目标章节的新 body 写入系统临时 UTF-8 文件，读取当前文件 SHA-256，生成
   `byteworker-kb-mutation/v1` 的 `replace_section` write。
4. 运行 `kb-mutate validate/execute`。工具负责固定章节校验、32 KiB 上限、原子写、journal、
   精确 commit 和回滚。
5. 收到 receipt 后回显修改后的相关章节供用户确认。

不得用空模板覆盖旧内容。发现明显过期条目时提示用户是否归档，不擅自删除。
