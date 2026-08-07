# byteworker · 重建、修复与恢复

> 由 `SKILL.md` 路由到这里。用户要求重建索引、检查链接、修复双链、恢复误删/错改,
> 或你怀疑派生物/链接图不一致时读取本文件。

若问题来自 skill/schema 持续升级、范围不止 INDEX/links,先按
[`references/doctor.md`](doctor.md) 运行全库只读兼容性扫描,再回到本文件执行对应维护动作。

## 数据不变量

**数据不变量(docs/development/DESIGN.md §1.C)**:`raw_data/` + `knowledge/` 节点 + `reports/` + `dashboard.md` 的 📌/⚠️ 手动项 = **真相源**;`INDEX.md` 与 `dashboard.md` 的派生部分 = **派生物**,可随时丢弃并从真相源 100% 重建。派生物与真相源不一致时,**永远重建派生物,绝不反向改真相源**。

## 重建 INDEX

**重建 INDEX(一等操作)** —— 不只在「文件数 ≠ 行数」时兜底触发;任何时候用户说「重建索引」「INDEX 不对」,或你怀疑 INDEX 与节点不一致,都可直接执行:

1. Agent / 自动化通过机器协议运行
   `bin/byteworker index rebuild --kb <数据目录> --dry-run` 预演，确认后去掉
   `--dry-run` 执行；人工排障仍可运行 `bin/rebuild-index.sh`。
2. 脚本扫 `knowledge/` 下全部 8 类节点的 frontmatter，并在存在时读取 body 首行 TL;DR，
   按 docs/development/DESIGN.md §6 的分节格式重新生成整个 `INDEX.md`。
3. 「定期摄取清单」优先由 `sources/` profiles 的 routine 派生；没有 profile 的旧来源才兼容
   `raw_data/` 的 `routine`。同一 `source_uid` 有 profile 时，profile 的启用/禁用和 cadence
   覆盖历史 raw。「群聊摄取进度」仍由 `feishu_chat` raw 的
   `source_chat_id` / `source_window` 派生。中断 raw(`digest_status: pending/failed`)不进入
   INDEX 表格,需要排障时直接扫 `raw_data/`。
4. apply 入口使用共享 KB 写锁，统一完成 `INDEX.md`、journal 和精确本地 commit；receipt 返回
   commit hash。任一步失败恢复文件、Git index 和必要的 HEAD。`--dry-run` 不写 journal/commit。

## 校验 / 修复双向链接

**校验 / 修复双向链接(一等操作)** —— `links` 是真相源、靠手工双向维护会漂移,而它撑着 `search` 的图遍历;任何时候用户说「检查链接」「修一下双链」,或你怀疑某次写入漏了反向链接,都可直接执行:

1. Agent / 自动化运行 `bin/byteworker doctor fix --only links --autolink`；预演加
   `--dry-run`。`bin/repair-links.sh` 只供人工底层排障。
2. 脚本确定性地补全缺失的反向链接(A→B 则补 B→A)、合并重复项并删除自链接；`--autolink` 还会扫描正文里出现的已存在节点 id 并补进 `links`。**悬空链接**(目标节点不存在)仍只报告、不改 —— 转告用户裁决(改 id 错字 / 补建节点 / 删该链接)。
3. doctor 事务统一处理节点、journal 与精确本地 commit；脚本退出码 3 表示仍有悬空链接待处理，
   事务可提交已完成的确定性修复并在复扫结果中保留该问题。

## 灾难恢复

数据目录是独立本地 git(docs/development/DESIGN.md §1.B):

- 误删 / 错改某节点 → `git restore <文件>` 或 `git checkout <commit> -- <文件>` 回滚。
- `INDEX.md` 损坏 / 丢失 → 直接「重建 INDEX」,无需动 git。
- 数据目录大范围损坏 → `git reflog` 找最近完好提交回滚(每次写操作都有提交,见 `references/write-rules.md` 回滚点)。
