# Dreaming maintenance：doctor 维护

只在 `run-due` 返回 `job=maintenance` 时加载。该 job 复用公开 doctor facade，不 import
`lib/doctor.py` 或复制修复逻辑。

## 执行

1. 运行 `bin/byteworker doctor scan --kb "<KB>"`，保存有限 summary 和 finding 元数据。
2. 若存在 `auto_fixable`，运行
   `bin/byteworker doctor fix --kb "<KB>" --only index,links`。该事务只执行 doctor 明确声明的
   确定性白名单修复，自带共享写锁、Git 回滚、复扫、journal 和本地 commit；不要手工改文件。
3. 再读取最终 scan/transaction。完全健康或只有无需用户处理的 info/低价值 warning 时，以
   `success` 完成；coverage checkpoint 只写计数与 commit，不写业务内容。

## 选择用户需要决策的信息

以下内容视为重要，按 error、证据/真相源风险、自动化阻断、其它 warning 排序，最多展示 10 项：

- 所有剩余 error，以及 transaction `status=decision` 的 reasons；
- 可能导致证据链、raw、provenance、节点身份或报告引用失真的 warning；
- 需要用户确认 Profile selector/capture policy、人员身份、悬空实体或冲突处置的 warning；
- 会阻断后续 digest、报告、Dreaming coverage 或确定性修复的问题。

只展示 `code/path/message/repair` 和计数，不粘贴节点正文、raw 或聊天内容。兼容性 info、已自动
修复项和纯格式噪声不打扰用户。

存在重要项时，通过当前宿主向用户给出一次有限决策摘要，然后：

```bash
bin/byteworker dreaming complete --kb "<KB>" --token "<lease.token>" \
  --run-status partial --error-code DOCTOR_USER_DECISION_REQUIRED \
  --coverage-checkpoint "doctor:error=<N>,warning=<N>,commit=<short-or-none>"
```

该错误进入 `waiting_for_user`，避免每个 tick 重复提醒。用户处理或明确忽略后，运行
`dreaming retry-job --job maintenance`。通知失败时也不得把问题标为已送达；用稳定失败码完成。

## 禁止

- 不得猜业务语义：不自动补业务字段、猜身份、删除悬空节点、创建/迁移 Profile 或扩大来源授权。
- 不把 severity 当作 auto-fix 依据；只有 finding 的 `auto_fix` 和 doctor 事务可以决定修复。
- 不 push，不绕过 KB dirty/staged blocker，不因 maintenance 失败阻塞其它 Dreaming job。
