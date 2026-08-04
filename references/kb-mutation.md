# byteworker · 非 digest 确定性写事务

update、thinking、context、dashboard、日报/周报/IM 报告统一使用
`byteworker-kb-mutation/v1`。业务 plan 与候选文件只能放系统临时目录或 KB，禁止进入 skill 仓库。

最小 plan：

```json
{
  "schema_version": "byteworker-kb-mutation/v1",
  "operation": "context",
  "conflict_disposition": "no_conflict",
  "conflict_evidence": [],
  "writes": [
    {
      "path": "context.md",
      "mode": "replace_section",
      "section": "我的当前重点",
      "content_path": "/tmp/context-section.md",
      "base_sha256": "sha256:<当前文件 hash>"
    }
  ],
  "journal": {
    "action": "context update",
    "summary": "用户更新当前重点"
  },
  "commit": {"message": "context: update current focus"}
}
```

write mode：

- `replace`：完整替换/新建目标；已有目标必须带当前 `base_sha256`，新建时为空。
- `replace_section`：只替换已登记固定章节的 body，适合 context/dashboard 手动项。
- `replace_preserving_sections`：用完整候选刷新报告/看板，但从旧文件保留
  `preserve_sections`。

允许目标仅为 `context.md`、`dashboard.md`、`knowledge/**/*.md`、`reports/**/*.md`。
operation 与目标一一对应：`context` → `context.md`，`dashboard` → `dashboard.md`，
`update` → `knowledge/**/*.md`，`report` → `reports/**/*.md`；write 未知字段一律拒绝。
thinking 节点属于 knowledge update，因此 plan 使用 `operation: "update"`。
raw/provenance/source/todo 不走本工具。knowledge write 必须按 `conflict-policy.md` 声明处置；
非 `no_conflict` 必须带证据。

```bash
bin/byteworker kb-mutate validate --kb "<KB>" --plan "<plan.json>"
bin/byteworker kb-mutate execute --kb "<KB>" --plan "<plan.json>"
```

execute 在共享 KB 写锁内重新校验 baseline，随后原子写候选、按需重建 INDEX、追加 journal、
精确暂存并创建本地 commit。任一步失败恢复文件和 Git index。只有 committed receipt 表示成功。
