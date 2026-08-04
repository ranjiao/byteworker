# Dreaming 启用前能力导览

只在用户准备启用 Dreaming 时加载。不能只展示一句成本提示；必须用自然语言完整讲清以下内容，
允许用户追问，并在导览结束后单独询问是否启用。

## 它与 digest 的区别

| | digest | Dreaming |
|---|---|---|
| 触发 | 用户给出明确资料或运行 routine | 宿主定时唤醒，主动检查已授权来源 |
| 目标 | 把一份资料可靠摄取为 raw、出处和实体图 | 发现变化、形成 Finding、报告或待确认动作 |
| 写知识 | 通过 SourceBundle + DigestTxn 直接完成 | 默认不入库；知识候选仍须重新采集并走 DigestTxn |
| 生命周期 | 一次事务，committed/noop 即结束 | 长期 job、lease、cursor、Finding、feedback 和 recovery |

Dreaming 不是“自动 digest 所有内容”，也不替代 search/update/todo。

## 能做什么

1. `process`：按 grant 采集 IM 等已支持来源，识别决策、责任、风险、变化和冲突，形成可复核
   Finding。
2. `review/explain/feedback`：查看为什么重要、证据窗口和 coverage，并标记有用、已知、错误、
   完成、延后或忽略。
3. `morning`：基于 committed Finding、Todo 和 KB 生成晨报；daily/weekly 只有完成独立 owner
   迁移后才接管。
4. Action Ledger：报告、提醒、Todo 候选、来源候选和知识候选先经过 policy、确认与 fencing；
   默认不会自动发消息、建任务或写长期知识。
5. `maintenance`：低频运行 doctor，自动修复 INDEX/确定性 links 等白名单问题；剩余重要问题只
   给出有限摘要，请用户决策，不猜业务语义。
6. `recovery`：补偿过期 lease、采集 gap、失败 receipt 和待投递事项。

## 默认值与授权

- 启用后默认开启 process、morning、maintenance、recovery；daily/weekly 默认关闭。
- IM grant 默认 `off`。`monitored` 只读已登记群聊；`all_visible` 还可能读取 P2P 和免打扰会话，
  必须再次确认。Finding 持久化也是独立授权。
- 报告持久化、即时提醒和知识归档分别授权。Todo、来源、冲突和知识候选仍可要求逐项确认。
- foreground `process once` 不等于启用后台，也不继承后台 grant。

## 成本、隐私与运行条件

- 会增加网络请求、模型 Token、私密 spool/state 和报告存储；具体消耗随来源量和频率变化。
- 本地机器和宿主任务必须保持开机、唤醒、联网；休眠/关机期间不运行，恢复后只能补跑。
- state 位于 KB 的 Git 排除目录，权限为 `0700/0600`；业务数据不进入 skill 仓库、不 push。
- coverage 不完整会明确标 partial；Dreaming 故障不阻塞 digest/search/update。

## 启用前对话

导览后依次确认：

1. 用户是否理解它是长期主动机制而非 digest 的快捷模式。
2. 是否接受机器运行条件与额外网络、模型、存储成本。
3. 是否现在启用。只有明确同意后才能同时传：
   `--acknowledge-capability-tour --acknowledge-machine-runtime`。

启用完成后回显默认开启/关闭的 job、IM 仍为 `off`，再询问要不要配置 monitored/all_visible、
Finding 持久化和报告动作；不得把这些授权捆绑进总开关。
