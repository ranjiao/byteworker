# byteworker · 唯一冲突策略

> digest 与 update 的冲突处理以本文件为唯一真相源。其它 reference 只引用这里，不得另写
> “较新来源自动覆盖”等平行规则。

| 类型 | 判定 | 动作 | `conflict_disposition` |
|---|---|---|---|
| 无冲突 | 新事实补充而不否定既有事实 | 正常合并 | `no_conflict` |
| 明确修订 | 同一来源明确声明新版修订旧版，且 revision 可验证 | 更新当前值，旧值进入历史并保留两版来源 | `revision` |
| 明确取代 | 生效 decision 明确 supersede 旧 decision | 旧节点设 `superseded`/`superseded_by` | `supersede` |
| 用户纠正 | 用户明确确认哪个值有效 | 按确认合并并记录用户裁决 | `user_confirmed` |
| 独立来源冲突 | 两个来源对同一事实给出不一致值，不能证明修订关系 | 并列展示证据，暂停写入并询问用户 | 不得执行 |

规则：

- 时间较新本身不构成 revision 或 supersede。
- 未经裁决不得删除旧事实、只引用新来源或把冲突写成确定结论。
- `revision`、`supersede`、`user_confirmed` 必须在 mutation/digest plan 中带可定位
  `conflict_evidence`；事务 validator 拒绝未声明的覆盖。
- 用户暂不裁决时，当前来源仍可保存为独立 raw；受冲突影响的知识节点不更新。
