# byteworker · update

> 仅在用户明确更新知识节点时加载。冲突只按 `conflict-policy.md` 处理，写入只走
> `kb-mutation.md`。

1. 用 `kb-query search` 定位目标节点并读取当前完整内容。
2. 用户带来外部文档、会议、聊天或其它来源时，先按标准 digest 保存 raw/provenance；不要把
   外部事实当作无来源的直接编辑。
3. 按 `conflict-policy.md` 分类：
   - 独立来源冲突：并列给用户看证据，暂停知识节点 mutation；
   - 明确 revision/supersede 或用户已裁决：保留旧值与来源，生成对应 disposition；
   - 时间较新但无修订关系：仍按冲突处理，不自动覆盖。
4. 生成完整候选节点。`sources`/`links` 去重，双向 links 完整，时间条目倒序，刷新
   `updated`；只有新输入或用户确认真正复核了当前事实才刷新 `last_verified`。更新 `area` 时
   还必须复核业务 / 团队 / 个人归属：标题、H1、TL;DR 与概述首句都写限定语，不把不同业务的
   来源、节奏、指标或技术共识合并成通用主题；无法确认归属时暂停 area mutation。更新内部
   `org` 时，先用实时飞书目录结果与 person 的 `department_path` 核对完整正式部门路径；名称
   冲突时请用户裁决。负责人只能来自用户确认或权威组织来源，未确认时询问用户并写“待用户确认”，
   不从成员、职级、作者或会议角色推断。同步 person/org 时把“通讯录当前归属”“管理职责”“汇报
   关系”分字段或分条记录并标明各自来源/日期；细粒度职责不能覆盖较粗 `department_path`。账号
   简称、异体姓名或英文名先按 `feishu_id` 消解，唯一命中才复用。目录只返回祖先路径、目录归属
   与管理职责不同，或历史材料只显示协作同现时，显式披露差异，不拼接部门、不推断成员/上下级。
   用户纠正当前归属时同步修正 TL;DR、基本信息、当前 links 与双向 links，旧关系降为带日期的
   历史协作，不删除旧来源。
5. 将候选放在系统临时目录，构造 `byteworker-kb-mutation/v1`，其中 knowledge write 必须带
   当前 `base_sha256`、`conflict_disposition` 和必要的 `conflict_evidence`。依次运行：

   ```bash
   bin/byteworker kb-mutate validate --kb "<KB>" --plan "<plan.json>"
   bin/byteworker kb-mutate execute --kb "<KB>" --plan "<plan.json>"
   ```

6. 只有收到 `status=committed` receipt 后才能声称更新完成。INDEX、journal、精确暂存、
   commit 和失败回滚均由 mutation 工具负责，Agent 不手工执行。
