# byteworker · Digest final reducer

只在 `digest-parallel merge` 已验证完整 shard coverage 后加载。reducer 只读 reduce packet、同一 run 的
conflict receipt、SourceBundle identity/anchors 和已有节点的定点快照；不重读完整 component、不扫描全库、
不启动 worker、不询问用户，也不直接写 KB。

1. 按 `dedupe_key`、source refs 和同源节点合并重复 record，保留每条 evidence anchor；不把语义相似
   自动当作同一事实。
2. 独立来源冲突、uncertain 依赖或缺证据项停止候选生成，返回固定阻塞码给 coordinator 一次性询问。
3. 按 `semantic-policy.md` 决定实体晋升，按 `write-rules.md` 完成标题消歧、area/org/person 治理和双向
   link 候选；不得从协作同现推导组织关系。
4. 生成一份完整 `digest-plan/v2`，只引用 SourceBundle 与 anchors，不复制 raw/source/provenance；所有
   新节点有 evidence，更新节点带 `base_sha256`。
5. reducer 只返回 plan 路径、record/node/evidence/warning 数量或稳定错误码。coordinator 通过
   `digest-flow commit` 提交；只有 receipt `status=committed|noop` 才是终态。
