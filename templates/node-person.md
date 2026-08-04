---
id: person-<slug>
title: <姓名>
type: person
feishu_id: <飞书英文 id;= 企业邮箱 @ 前缀,全局唯一;实体消解主键;不参与 id;新建前必须解析出来>
enterprise_email: <通讯录返回的企业邮箱;无或不可见时省略>
department_path: <飞书通讯录当前部门路径;无或不可见时省略>
directory_verified_at: <本次通讯录核验时间;ISO8601,新建/更新 person 必填>
tags: []
status: current
created: <YYYY-MM-DD>
updated: <YYYY-MM-DD>
last_verified: <YYYY-MM-DD>
sources:
  - <raw-id 或飞书链接>
primary_source: <有明确主要资料时填 raw-id,否则省略>
primary_source_url: <由 digest 事务物化,无则省略>
links: []
---

# <姓名>

> **TL;DR:** <一句话:这个人是谁、和我是什么协作关系>

<!-- 关键事实句尾写 [E1] 等;不要手写“证据”章节,由 digest 事务生成。 -->

## 基本信息
<!-- 角色 / 当前所属团队 / 对接方式。通讯录字段按 directory_verified_at 标注核验时间；
     department_path 变化时更新“通讯录当前归属”，并在“协作历史与关键交互”保留带日期的原团队。
     “通讯录当前归属”“管理职责”“汇报关系”分条记录来源与日期，互不推导；用户确认的细粒度职责
     不得覆盖较粗的 department_path。账号简称/异体姓名/英文名先按 feishu_id 唯一消解，避免重复人物。 -->

## 负责什么
<!-- ta 负责的项目、领域、组织；组织负责人只写用户确认或权威来源，并记录确认日期 -->

## 协作历史与关键交互
<!-- 重要的会议、决策、协作节点;按事件发生时间倒序 -->

## 立场 / 利益 / 动机
<!-- 跨多次讨论沉淀的立场倾向、核心利益诉求、行为逻辑;由各 event 的「参与方立场分析」累积。
     须有证据支撑；动机/利益只有直接自述或至少两条独立观察时才可标【推断】,否则省略。
     详见 references/semantic-policy.md 与 references/digest-analysis.md。 -->

## 偏好 / 风格 / 注意点
<!-- 沟通偏好、工作风格、需要注意的点 -->

## 关联节点
<!-- links 中的 project / org / event / decision,简述关系类型；历史协作 link 不表示当前成员归属 -->
