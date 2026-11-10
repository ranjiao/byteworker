# byteworker · Workflow token 预算

`references/workflow-routes.json` v2 为每个 workflow 分离声明静态规则、worker prompt、动态 context、
来源 packet、总输入和输出预算。CI 对每个 source type、全部 feature 与错误闭包执行实际展开；不得再把
reference 字符数称为 token。

## 按需加载路由

Agent 用只读命令选择本次闭包，无需整读 manifest，也无需先读本预算手册：

```bash
bin/byteworker workflow-budget resolve --workflow digest --source-type feishu_doc
```

回执为 `byteworker-workflow-route/v1`，只含所选 workflow/source/features、规则和 worker prompt 路径，
不读取规则正文、不计算 token、不访问 KB/runtime/provider。Agent 只加载返回的路径；已读路径去重。
普通 digest 的必读是 core/flow/inline 与安全语义 policy。白板、内部资料、立场、routine 分别加
whiteboard/internal_reading/stakeholder_analysis/routine。planner 选择 parallel 才加 parallel_execution；
恢复或 usage 上报加 recovery；多来源原子提交加 batch。失败加 --include-on-error。
新条件出现时重新 resolve（保留此前已触发的 feature），只补读新增文件，不跳过规则。
large worker、Wiki resume 与 Dreaming routine 的完整入口仍独立自足，不能依赖主对话的已读状态。

## 完整预算检查

运行时只有在创建隔离 worker、输入接近 workflow 限额或需要解释超预算时调用：

```bash
bin/byteworker workflow-budget inspect --workflow digest \
  --source-type feishu_doc --feature comments --feature whiteboard \
  --context "<CONTEXT_PROJECTION>" --source-packet "<PRIVATE_PACKET>"
```

预算器优先使用固定 `o200k_base` tokenizer；运行环境没有该可选库时，回执明确标记
`byteworker-conservative-token-estimate/v1` 与 `exact=false`。两种情况都只返回计数、方法、闭包路径和
固定 action，不回显动态文件内容。

`status=over_budget` 时必须执行 manifest 的 `overflow_action`，例如缩小 Wiki 范围、归档/压缩 context、
把来源 packet 继续分片或路由到 large-input；不得静默截断来源、evidence 或规则。输出预算是生成上限，
不并入输入 token；缓存输入只属于实际 usage ledger，不改变路由预算。

## 超限时判断与确认

先看 `measurement` 和 `review.exceeded_limits`，指出是哪部分超了、计数是否估算。检查本阶段
是否混入其它阶段的输入、重复规则或证据索引，优先复用现有私有产物、按角色投影和分片。
用同一方法重算，并比较分片后单次峰值及所有调用的预计总消耗。只删重复表示或无关上下文，
保留已确认范围和证据。planner 返回 `inline` 不构成预算通过。

用户已确认的范围能在现有预算内完整完成，就直接继续。Dreaming 无人值守任务按既定预算执行，
拆分后仍超限时仅暂缓该来源，保留进度并在运行摘要记录缺口。交互任务确有额外消耗才可完成时，先确认
宿主模型可用输入容量，预留生成和其它上下文，再向用户说明当前用量、必要性、建议的新上限
或预计总消耗，并让用户选择这次放宽、调整范围或暂缓。超过宿主容量时只提供拆分、换可用宿主
或调整范围。规则、worker prompt、禁止携带的输入和输出上限不能用本例外放宽。

`review.proposed_changes` 给出动态输入和必要的总输入上限的最小增量。只有用户明确确认后，
对同一 workflow、feature、规则和输入再次运行 `inspect`，附
`--approved-review-id "<review.id>" --host-input-limit "<已核实的可用输入 token>"`。
内容或配置变化须重新核算。回执只对这次输入有效，不保存授权，不修改默认预算。
如果只需接受分批的额外总消耗，各批仍按原上限检查，不使用例外参数。
