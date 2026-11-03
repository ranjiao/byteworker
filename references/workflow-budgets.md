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
