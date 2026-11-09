# Dreaming maintenance：doctor 维护

只在 `run-due` 返回 `job=maintenance` 时加载。该 job 复用公开 `doctor scan/fix` facade，
不 import Doctor 内部模块，也不另设修复白名单。

## 执行

1. 调用 `bin/byteworker doctor scan --kb "<KB>"`。退出码 2 表示扫描完成且有问题。
2. 如有 `auto_fix` 为 `index` 或 `links` 的项，调用
   `bin/byteworker doctor fix --kb "<KB>" --only index,links`，以它的复扫结果为准。
   维护不传 `--autolink`。安全修复自带共享写锁、Git 回滚、复扫、journal 和本地 commit。
   某个动作被其依赖阻断时，独立的安全动作仍可执行。
3. 把最终回执的 `data.maintenance_issues` 完整复制到结果文档的 `issues`。必须使用最后一次完整扫描的列表；
   空列表表示问题已清除。不要自行筛选、截断或把扫描失败当作空列表。
   若 facade 返回私密输出 artifact，先按机器协议读取并核验其中的 JSON，再提取所需字段。
4. 通过 `dreaming complete --run-status success --result-input "<临时运行结果 JSON>"` 完成租约。
   `success` 表示本轮维护执行完成，允许仍有单条问题待处理。Doctor 退出 1、无法取得完整回执或
   `transaction.execution_failed=true` 时，按技术失败完成并使用退避，不提交 `issues`。
5. 完成回执的 `maintenance.new` 最多包含 10 个首次出现或内容变化的问题。只向当前宿主任务结果
   汇总这些项和总数。未变化的项继续留在 `dreaming status` 的 `maintenance_issues`，后续扫描
   会移除已消失的项。不要发送额外消息或重复提醒。

临时结果文档位于 KB 外，使用 `byteworker-dreaming-run-result/v1`，含 lease 的 `job/period`、
一句话 `summary`、`checks[]`、`repairs[]` 和本轮完整 `issues[]`。检查项记录安全修复是否被
结构或 Git 工作树条件阻断。修复项只记录实际变更的 KB 相对路径、问题码、动作和结果。问题项
仅含 Doctor 生成的 `id/code/path/severity`；描述变化产生新的 id。不得将节点正文、raw 内容、
聊天消息、URL token、完整 diff 或 stdout/stderr 放入结果。

旧状态的 `DOCTOR_USER_DECISION_REQUIRED` 仅用于识别旧版整任务 `waiting_for_user`。升级后
按既有计划继续扫描，逐项重建待处理列表。

## 禁止

- 不得猜业务语义，不自动补业务字段、猜身份、删除悬空节点、创建或迁移 Profile、扩大来源授权。
- 不以 severity 决定修复权限。只执行 Doctor 在选定范围内允许的动作。
- 不 push，不绕过 KB dirty/staged blocker，不因 maintenance 失败阻塞其它 Dreaming job。
