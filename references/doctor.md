# byteworker · doctor 兼容性检查与修复

> 由 `SKILL.md` 的 `doctor` 路由到这里。用于 skill / schema 持续演进后检查已有知识库是否仍与
> 当前代码兼容。

## 原则

- `scan` 永远只读；以当前 `docs/development/DESIGN.md`、节点/报告模板和代码常量生成目标 profile 与 fingerprint。
- 把“兼容历史格式”与“损坏/不兼容”分开。历史 raw 没有最新 `payload_schema` 只记兼容信息，
  不强迫改写不可变原文。
- 只自动修复可确定重建的问题：`INDEX.md`、重复 links、缺失反向 links；加
  `--autolink` 才补正文提及的节点边。
- 缺失业务字段、悬空 id、证据链断裂、真相源缺失、person 身份未解析等只报告并给建议，
  绝不让脚本猜业务语义。
- Profile 创建或迁移需要确认 selector/capture policy，永远不属于 `doctor fix` 或
  post-update auto-fix；doctor 只报告缺口和严重程度。

## skill 更新后的自动检查

`bin/update-check.sh` 只有在 Git fast-forward 确实产生新版本后才调用
`bin/update-postflight.py`。postflight pending / retry 状态由更新器独立持久化，不维护迁移
数据库或常驻锁：

1. 用更新后的代码只读扫描；
2. 自动执行 finding 明确声明的全部 `auto_fix`，不按 error/warning 猜测可修性；
3. 写入前确认知识库有本地 Git、没有 staged 变更，且目标节点、INDEX、当天 journal
   没有未提交编辑；知识库位于 skill 仓库/配置 remote、重复 ID、frontmatter 损坏等会让
   确定性修复失真的结构错误也直接阻断；
4. 复扫、追加一条维护 journal，只暂存实际修复路径并创建本地提交，永不 push；
5. 只输出一行：剩余 error / 执行失败请求用户决策；只剩 warning/info 时给数量摘要，由用户
   选择忽略或立即处理；完全健康则只确认通过。

`.kbconfig` 尚未配置时静默跳过，因为没有知识库可检查。相关文件正被编辑时不抢写，把它视为
无法自动完成的严重问题交给用户，而不是覆盖或卷入现有改动。

## Dreaming 周期维护

Dreaming 启用后的 `maintenance` job 复用本文件同一 scan/fix facade 和事务边界，不能定义第二套
修复白名单。细则见 `references/dreaming-maintenance.md`：只执行 finding 明确声明的
`auto_fix`，复扫后把重要未决问题用有限元数据摘要交给用户；用户处理或忽略前进入
`waiting_for_user`，避免重复提醒。maintenance 失败不改变 doctor 结果，也不阻塞其它能力。

## 用法

```bash
# 默认读取 .kbconfig
bin/byteworker doctor scan

# 先预演，再执行确定性修复
bin/byteworker doctor fix --dry-run
bin/byteworker doctor fix

# 只修一个维度；正文 auto-link 必须显式开启
bin/byteworker doctor fix --only index
bin/byteworker doctor fix --only links --autolink

# 测试或其它知识库
bin/byteworker doctor scan --kb /absolute/path/to/kb
```

统一 envelope 中 `status=attention` 表示扫描完成但仍有 finding。退出码仍为：`0` 没有
error/warning；`2` 扫描完成但仍有问题；`1` 参数、目录或执行失败。`info` 是明确兼容的历史
格式，不单独导致非零。人工排障仍可直接运行 `python3 bin/doctor.py ...` 查看原始输出。

## 检查范围

- 目录、不可派生真相源文件、本地 Git/remote、事务临时残留；
- 8 类节点的 frontmatter、type/path/id、日期、必需章节、TL;DR、person `feishu_id`，以及已有
  通讯录字段的 `directory_verified_at` / 企业邮箱格式；历史 person 完全没有通讯录字段时保持兼容，
  等真实查询后再补，不由 doctor 猜写；
- `thinking` 只校验最小 frontmatter、`effective|inactive` 状态、标题和非空正文，不要求
  sources、last_verified、TL;DR 或固定章节；
- raw 的 id、当前/历史 payload schema、状态、hash、digest targets；
- `sources/*.json` 的 Profile v1/v2 schema、凭据、规范路径、重复 `source_uid`；
- 定期来源的 Profile 覆盖和稳定 identity：Meego/Aeolus 缺 Profile 为 error，飞书文档
  legacy raw 为 warning，群聊/Base 等尚无 Profile schema 的来源为 info；
- raw 的 Profile path/revision 成对性、source identity，以及 component/digest key 和可选
  `byteworker-record-index/v1` 持久化契约；
- provenance JSON schema、raw/path/hash/source anchor；
- 节点 sources/primary source、`[E]` 标记/证据表/raw/anchor 的闭环；
- links 重复、自链接、悬空、反向边、正文已存在节点 id 的漏登记；其中自链接属于可确定
  删除的 error，悬空链接仍需人工裁决；
- 日报/周报/IM 报告 `[S]` 正文标记与「引用」条目；
- `INDEX.md` 与当前确定性重建结果。

SourceBundle 和 DigestPlan 属于事务临时输入，成功后可以删除；doctor 不要求它们留在知识库。
它验证的是 Profile → raw metadata/record index → provenance 的持久化结果。历史 raw 引用的
Profile revision 与当前 revision 不同可以是正常重配，不得据此改写不可变 raw。

## 执行修复后的收尾

`doctor fix` 与 post-update doctor 使用同一共享 KB 写事务：锁内重验工作树，只执行 index/links
白名单修复，复扫后统一写 journal、精确暂存并创建本地 commit。失败恢复文件、Git index 和必要的
HEAD；用户或 Agent 不再手工补收尾。`--dry-run` 始终只读。仍需人工裁决的问题保留在复扫报告中，
知识库永不 push。
