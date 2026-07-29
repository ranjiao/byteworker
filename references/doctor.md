# byteworker · doctor 兼容性检查与修复

> 由 `SKILL.md` 的 `doctor` 路由到这里。用于 skill / schema 持续演进后检查已有知识库是否仍与
> 当前代码兼容。

## 原则

- `scan` 永远只读；以当前 `DESIGN.md`、节点/报告模板和代码常量生成目标 profile 与 fingerprint。
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

## 用法

```bash
# 默认读取 .kbconfig
python3 bin/byteworker-cli.py doctor scan

# 先预演，再执行确定性修复
python3 bin/byteworker-cli.py doctor fix --dry-run
python3 bin/byteworker-cli.py doctor fix

# 只修一个维度；正文 auto-link 必须显式开启
python3 bin/byteworker-cli.py doctor fix --only index
python3 bin/byteworker-cli.py doctor fix --only links --autolink

# 测试或其它知识库
python3 bin/byteworker-cli.py doctor scan --kb /absolute/path/to/kb
```

统一 envelope 中 `status=attention` 表示扫描完成但仍有 finding。退出码仍为：`0` 没有
error/warning；`2` 扫描完成但仍有问题；`1` 参数、目录或执行失败。`info` 是明确兼容的历史
格式，不单独导致非零。人工排障仍可直接运行 `python3 bin/doctor.py ...` 查看原始输出。

## 检查范围

- 目录、不可派生真相源文件、本地 Git/remote、事务临时残留；
- 7 类节点的 frontmatter、type/path/id、日期、必需章节、TL;DR、person `feishu_id`；
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

doctor 不碰知识库 Git 和 journal。实际执行 `fix` 后：

1. 重新看 doctor 报告，区分已修复与仍需人工裁决的问题；
2. 用 `git -C <知识库> diff -- <本次路径>` 核对；
3. 按 `references/write-rules.md` 追加 journal，只暂存本次 doctor 修改的路径并创建本地提交；
4. 绝不 push 知识库数据目录。

上段针对用户主动运行 `doctor fix`；post-update doctor 的 journal 与本地提交由
`bin/update-postflight.py` 自动完成。
