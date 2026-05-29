# byteworker · 重建、修复与恢复

> 由 `SKILL.md` 路由到这里。用户要求重建索引、检查链接、修复双链、恢复误删/错改,
> 或你怀疑派生物/链接图不一致时读取本文件。

## 数据不变量

**数据不变量(DESIGN.md §1.C)**:`raw_data/` + `knowledge/` 节点 + `reports/` + `dashboard.md` 的 📌/⚠️ 手动项 = **真相源**;`INDEX.md` 与 `dashboard.md` 的派生部分 = **派生物**,可随时丢弃并从真相源 100% 重建。派生物与真相源不一致时,**永远重建派生物,绝不反向改真相源**。

## 重建 INDEX

**重建 INDEX(一等操作)** —— 不只在「文件数 ≠ 行数」时兜底触发;任何时候用户说「重建索引」「INDEX 不对」,或你怀疑 INDEX 与节点不一致,都可直接执行:

1. 在本 skill 目录运行 `bin/rebuild-index.sh`(先看不写加 `--dry-run`;测试 / 指定库加 `--kb <数据目录>`)。
2. 脚本扫 `knowledge/` 下全部 7 类节点的 frontmatter + body 首行 TL;DR,按 DESIGN.md §6 的分节格式重新生成整个 `INDEX.md`。
3. 「定期摄取清单」由 `raw_data/` 中带 `routine` 的 raw frontmatter 派生;「群聊摄取进度」由 `feishu_chat` raw 的 `source_chat_id` / `source_window` 派生。中断 raw(`digest_status: pending/failed`)不再进入 INDEX 表格,需要排障时直接扫 `raw_data/`。
4. 脚本原子写入(temp-then-move),只改 `INDEX.md`;由调用方按 `references/write-rules.md` 追加 journal 并精确暂存本次改动后提交。

## 校验 / 修复双向链接

**校验 / 修复双向链接(一等操作)** —— `links` 是真相源、靠手工双向维护会漂移,而它撑着 `search` 的图遍历;任何时候用户说「检查链接」「修一下双链」,或你怀疑某次写入漏了反向链接,都可直接执行:

1. 在本 skill 目录运行 `bin/repair-links.sh --autolink`(先看不写加 `--dry-run`;测试 / 指定库加 `--kb <数据目录>`)。
2. 脚本确定性地补全缺失的反向链接(A→B 则补 B→A)、合并重复项;`--autolink` 还会扫描正文里出现的已存在节点 id 并补进 `links`。**悬空链接**(目标节点不存在)与**自链接**只报告、不改 —— 转告用户裁决(改 id 错字 / 补建节点 / 删该链接)。
3. 脚本只动节点文件、不碰 git 与 journal —— 按 `references/write-rules.md` 由你在数据目录精确暂存本次脚本改动并提交,再追加 journal 收尾;脚本退出码 3 表示仍有悬空链接待处理。

## 灾难恢复

数据目录是独立本地 git(DESIGN.md §1.B):

- 误删 / 错改某节点 → `git restore <文件>` 或 `git checkout <commit> -- <文件>` 回滚。
- `INDEX.md` 损坏 / 丢失 → 直接「重建 INDEX」,无需动 git。
- 数据目录大范围损坏 → `git reflog` 找最近完好提交回滚(每次写操作都有提交,见 `references/write-rules.md` 回滚点)。
