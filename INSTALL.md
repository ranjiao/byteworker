# 安装 Byteworker

可以把本页链接交给 AI 助手，让它安装 Byteworker 或修复旧安装。自己操作请看「人工安装」。

## 给 AI 助手的安装步骤

仓库地址是 `https://github.com/ranjiao/byteworker`，克隆地址是 `https://github.com/ranjiao/byteworker.git`。
用 Git 克隆安装，并保留 `.git` 和有效的 `origin`，供自动更新使用。

### 1. 选择安装范围并判定 skills 目录

先让用户明确选择安装范围。用户确认前，禁止克隆、删除残留或创建软链接。

- **全局安装**适合本机多个工具共用。请用户选一个实际存放仓库的目录，默认 `~/byteworker`。将它设为
  `SOURCE_DIR` 和 `TARGET`。检测本机工具并展示结果。安装成功后，将各工具的 `$SKILLS_DIR/byteworker`
  链接到 `TARGET`。用户可以补充未检测到的目录。
- **局部安装**只给当前工具使用。按下表选当前工具的 `SKILLS_DIR`，再设置
  `TARGET="$SKILLS_DIR/byteworker"`。

全局安装的检测规则：

- 当前正在运行的 agent 环境必须纳入链接目标，即使对应配置目录尚未存在。
- TraeWork / TraeCode / TRAE IDE：检测到 `~/.trae-cn`，或当前环境属于 TRAE 产品家族。
- Codex：检测到 `${CODEX_HOME:-$HOME/.codex}`，或当前环境是 Codex。
- Claude Code：检测到 `~/.claude`，或当前环境是 Claude Code。
- OpenClaw：检测到 `~/.openclaw`，或当前环境是 OpenClaw。
- 其它工具只有在用户明确给出其 skills 目录时才纳入。

| 宿主 agent | skills 目录(SKILLS_DIR)|
|-----------|--------------------------|
| TraeWork / TraeCode / TRAE IDE | `~/.trae-cn/skills` |
| Codex | `${CODEX_HOME:-$HOME/.codex}/skills` |
| Claude Code | `~/.claude/skills` |
| OpenClaw | `~/.openclaw/skills` —— **装这里**(本机所有 agent 可见);装好后**务必**再做下面的「OpenClaw 专项」 |
| 其它 | 该 agent 发现 skill / `SKILL.md` 的目录;不确定就问用户 |

对每个要使用的 `SKILLS_DIR` 执行 `mkdir -p "$SKILLS_DIR"`。全局安装时还要确认
`SOURCE_DIR` 的父目录存在；不存在则先创建父目录。

### 2. 检查旧安装

安装前检查 `TARGET`，按实际情况处理。

- **局部安装且 `TARGET` 是符号链接**(旧式 symlink 安装)→ `rm "$TARGET"` 删掉链接本身
  (不动它指向的真实目录)。继续。
- **全局安装且 `TARGET` 是符号链接** → 不要把源目录建立在符号链接上。先问用户是否换一个真实
  `SOURCE_DIR`；只有用户明确同意删除该链接时才 `rm "$TARGET"`。
- **`TARGET` 是目录、且是合法 byteworker 克隆**(同时存在 `TARGET/.git` 与 `TARGET/SKILL.md`)
  → 已装过,更新即可:
  - 若 `git -C "$TARGET" remote get-url origin` 没有 origin →
    `git -C "$TARGET" remote add origin https://github.com/ranjiao/byteworker.git`
  - `git -C "$TARGET" pull --ff-only`(失败不致命,跳过)
  - 直接跳到第 3 步；按第 3 步说明跳过 clone，并在全局安装时创建 / 刷新软链接。
- **`TARGET` 是目录、但不是合法克隆**(缺 `.git` 或缺 `SKILL.md`,即半成品 / 空目录)
  → 残留。**先保住用户数据**:若存在 `TARGET/.kbconfig`,复制到临时处;
  然后 `rm -rf "$TARGET"`,继续第 3 步重装,装完把 `.kbconfig` 放回 `TARGET/`。
- **`TARGET` 不存在** → 干净安装,继续第 3 步。
- 全局安装时还要检查每个链接目标 `$SKILLS_DIR/byteworker`：
  - 若已经是指向 `TARGET` 的符号链接 → 保留。
  - 若是悬空符号链接或指向其它 byteworker 安装 → 先说明将替换该链接，只删除链接本身。
  - 若是非符号链接目录且是合法 byteworker 克隆 → 这是另一份真实安装，先问用户是否改为共用
    `TARGET`；用户同意后再迁移或删除，不能静默覆盖。
  - 若是非符号链接目录且不是合法克隆 → 按半成品残留处理，先保住 `.kbconfig` 再清理。
- 检查旧式安装路径 `~/byteworker`。合法克隆可作为全局安装的默认来源。不完整的目录按上面的残留规则清理。

### 3. 克隆或复用仓库

第 2 步找到合法克隆时直接复用。只有 `TARGET` 不存在或不完整的旧目录已清理时才克隆。

```bash
git clone https://github.com/ranjiao/byteworker.git "$TARGET"
```

全局安装时，clone 或更新 `TARGET` 后，对自动检测到或用户补充的每个 `SKILLS_DIR` 创建软链接：

```bash
mkdir -p "$SKILLS_DIR"
ln -sfn "$TARGET" "$SKILLS_DIR/byteworker"
```

局部安装只配置当前工具。告诉用户其他工具需要单独安装或链接。

- 缺少 `git` 时先安装。macOS 用 `brew install git`，Debian/Ubuntu 用 `sudo apt install git`。
- 克隆遇到网络错误时，说明实际错误，并按「沙箱与云环境」排查。保留 Git 克隆的安装方式，自动更新依赖有效的 `origin`。
- 若第 2 步保存过 `.kbconfig`,现在放回 `TARGET/.kbconfig`。

### 4. 检查依赖

```bash
"$TARGET/bin/check-deps.sh"
```

先补齐必需依赖 `git`、`jq`、`bash` 和 `python3 >= 3.10`。macOS 可用 `brew`，Linux 可用 `apt`。

- Node、`lark-cli`、`meegle` 可以等到使用对应来源时再装。
  按[飞书 CLI 官方安装指南](https://open.feishu.cn/document/no_class/mcp-archive/feishu-cli-installation-guide.md)
  装 `lark-cli` 与 `lark-doc / minutes / vc / im / calendar / contact / base` 等 skill。
  摄取 Meego 保存视图时另装 `meegle` 与 `meegle` skill。登录和最小授权放到下一步,
  不要在用户未选择前自动打开 OAuth。
  风神读取由 byteworker 自带的标准库只读客户端完成，不需要安装额外 CLI；凭据配置放到下一步。

`check-deps.sh` 和 `bin/byteworker preflight` 用同一套依赖检查。它会核对 Python 版本和
`zoneinfo`，并从 PATH、`~/.local/bin`、Volta、Homebrew 和已安装的 NVM 版本中查找可用的
Node、`lark-cli` 和 `meegle`。当前会话的 `command -v` 只反映当前 PATH。

安装或首次启动有阻塞时，使用 `bin/byteworker preflight --interactive` 查看诊断编号。
用 `bin/byteworker doctor feedback --run-id "<编号>"` 生成邮件草稿。反馈只含技术状态。
Python 未启动时，先查看宿主的本地错误或任务历史。

### 5. 询问可选来源授权

先运行只读检查，查看 Meego、多维表格和风神的授权状态。

```bash
"$TARGET/bin/byteworker" source auth-status --source-type meego
"$TARGET/bin/byteworker" source auth-status --source-type feishu_base
"$TARGET/bin/byteworker" source auth-status --source-type aeolus
```

命令返回 `status=success` 后，看 `data.ready` 判断授权是否就绪。只要有一个所选来源未就绪，就先询问用户。

> 是否现在启用 Meego / 多维表格 / 风神定期摄取？它们需要用户授权。可以选择任意来源，
> 或“稍后再说”；跳过不影响 byteworker 其它能力。

只为用户选中的来源继续:

- **Meego** 使用独立的 `meegle` OAuth。先从用户给的资源 URL 确定 host；没有 URL 就问
  `project.feishu.cn`、`meegle.com` 还是自定义域名。然后执行
  `meegle auth login --host <host>`，等待浏览器授权完成，再运行一次 `source auth-status`
  验证。不得替用户猜站点。
- **多维表格** 复用 `lark-cli` 用户身份，但另需 Base 摄取的 5 个最小只读 scope。
  以 `source auth-status` 返回的 `data.action.command` 为准发起 split-flow；当前命令形如:

  ```bash
  "$TARGET/bin/byteworker" lark auth login \
    --scope "base:app:read base:table:read base:field:read base:view:read base:record:read" \
    --no-wait --json
  ```

  若 `lark-cli` 尚未初始化,先运行 `"$TARGET/bin/byteworker" lark config init --new`。发起授权后,把返回的
  `verification_url` **原样**展示给用户,并运行
  `"$TARGET/bin/byteworker" lark auth qrcode "<verification_url>" --output "<cwd 下的相对 PNG 路径>"`
  展示二维码；本轮到此结束。用户回复已完成后,由安装助手执行
  `"$TARGET/bin/byteworker" lark auth login --device-code <本次流程返回的 device_code>` 收尾,再运行
  `source auth-status` 验证。URL / device code 只用于这一次进行中的授权,不得写入
  skill 仓库或知识库。
- **风神** 使用 byteworker 原生只读客户端，不依赖其它 CLI。凭据按以下优先级选择一种：
  `BYTEWORKER_AEOLUS_TITAN_PASSPORT`（当前用户态会话）、
  `BYTEWORKER_AEOLUS_BYTECLOUD_JWT`（运行时在内存中交换用户态会话）、
  `BYTEWORKER_AEOLUS_BEARER_TOKEN`，或适合定时任务的
  `BYTEWORKER_AEOLUS_CLIENT_ID` + `BYTEWORKER_AEOLUS_CLIENT_SECRET`。服务态凭据必须单独获得
  目标看板 / dataset 的读取权限，不能假定等同于当前用户。

  交互环境可由进程环境或 secret manager 注入；长期本地任务也可使用
  `~/.config/byteworker/aeolus-auth.json`（或通过 `BYTEWORKER_AEOLUS_AUTH_FILE` 指定仓库外路径）：

  ```json
  {
    "client_id": "<风神只读 client id>",
    "client_secret": "<风神只读 client secret>"
  }
  ```

  凭据文件必须 `chmod 600`，父目录建议 `chmod 700`。也可把键换成
  `titan_passport`、`bytecloud_jwt` 或 `bearer_token`。不得把凭据写入 skill 仓库、
  知识库、raw snapshot、日志或命令参数。配置后重新运行
  `source auth-status --source-type aeolus`，以 `data.ready=true` 为准。定时任务只检查和使用
  已注入凭据，不自动登录。

Base 返回 `91403`，或 Meego 空间、风神看板返回 Permission Denied 时，请资源所有者给当前用户开读取权限。
不要重复登录或自动改用 bot。

### 6. 验证并收尾

- 确认 `"$TARGET/SKILL.md"` 存在。
- 安装时读取 `"$TARGET/TUTORIAL.md"`，完成首次设置。
  1. 设置并初始化持久、私密的知识库数据目录，写入 `"$TARGET/.kbconfig"`。
  2. 填写 `context.md` 的用户姓名 / 别名 / feishu_id / 时区 / 交互语言、职责范围、团队边界。
     交互语言必须用当前对话语言向用户确认；命令、URL 或资料原文不能作为默认语言依据。
  3. 填写重点人物、重点项目、主管方向和其它关注重点。
  4. 摄取 / 查询演示可以跳过，但前三项不能因为没有示例文档而跳过。
- 初始化完成后，读取 `"$TARGET/references/report-scheduling.md"`，通过机器协议检查：

  ```bash
  "$TARGET/bin/byteworker" report-automation status \
    --kb "<知识库绝对路径>"
  ```

- `needs_onboarding=true` 时询问：

  展示问题前先调用 `report-automation decision --value prompted`，保证这次提示即使未获回答也
  不会在之后每轮重复出现。

  > 要现在设置自动日报和周报吗？默认工作日 20:30 生成当天日报，周一 09:30 生成上一完整 ISO 周的周报。
  > 两种报告都在本地运行，先更新所有已登记的定期来源，再生成报告。日期、时间和通知可以改，也可以稍后再设置。

- 用户拒绝或稍后时，把 `prompted` 分别更新为 `declined` / `deferred`；不创建任务，也不在
  之后每次打扰。
- 用户同意时：
  1. 核对 `context.md` 时区和 routine 来源的无人值守授权。
  2. 选择知识库数据目录作为任务项目，且只能用**本地**运行环境。
  3. 创建前搜索同名 / 同 prompt 任务，优先更新，不重复创建。
  4. 日报使用 `templates/report-automation-daily.md`，周报使用
     `templates/report-automation-weekly.md`；另建
     `templates/report-automation-recovery.md` 补偿任务，默认每天
     08:30、12:30、18:30、22:30 检查失败或错过的 period。
  5. 立即 Run now 验证一次；只有报告、journal 和知识库本地 commit 真实完成后才调用
     `report-automation configure` 记录 configured。
- 当前宿主的具体设置：
  - **Codex 桌面端**：创建 standalone cron automation，知识库作为 project，
    `executionEnvironment=local`；不用 heartbeat、不用 worktree。需要电脑开机且应用运行。
  - **Claude Code Desktop**：创建 Local scheduled task，folder 选择知识库。不要用 `/loop`
    或云端 `/schedule` Routine。
  - **TRAE Work 桌面端**：先把知识库绝对路径作为工作目录加入当前 TraeWork 项目；只加入
    byteworker skill 仓库或配置 `.kbconfig`，不会授予 TraeWork Sandbox 对 KB 的访问权限。
    再在“自动化”中选择本地环境和该知识库目录；可在对话中创建或手工创建。不使用 Web / 云端
    任务。当前没有可依赖的公开稳定管理 API 时，以“已配置”和执行历史核验，不伪造 task ID。
    如果出现 `Operation not permitted` / `Permission denied`，且 skill 仓库可访问而 KB 不可
    访问，先检查 KB 是否已加入项目工作目录；不要用 `sudo`、`chmod` 或复制 KB 绕过 Sandbox。
  - **TraeCode / TRAE IDE**：只能安装并手动调用 byteworker skill；没有可用的本地定时任务
    运行机制。安装助手必须提醒用户：不能在 TraeCode 自动创建日报 / 周报更新任务，也不能运行
    Dreaming。若用户需要这些自动运行能力，请改用 TraeWork 桌面端打开同一
    知识库目录后继续设置。
  - **其它宿主**：没有经核实的原生定时能力时，只展示 prompt 和缺口；不要擅自改用系统
    cron / launchd。
- 若用户选择“稍后再说”,明确告诉他:第一次摄取 Meego / Base / 风神时 skill 会再次给出同样的
  登录或最小 scope 授权引导；以后说“设置自动报告”可重新进入定时任务配置。
- 提醒用户选择持久、私密的知识库目录。沙箱临时目录可能被回收。
- 说明安装和首次设置结果。列出已创建的任务、绝对执行时间、运行环境和首次验证结果。
  任务未创建或验证失败时说明实际状态。

---

## Codex 专项:确保被自动发现

宿主是 Codex 时,确保 `${CODEX_HOME:-$HOME/.codex}/skills/byteworker` 存在即可被自动发现：
全局安装时它通常是指向 `SOURCE_DIR` 的符号链接；局部安装时它是直接 clone 出来的真实目录。
本仓库的 Codex 兼容入口是 `SKILL.md`:

- `SKILL.md` frontmatter 只保留 `name` 与 `description`,这是 Codex 的触发依据。
- `agents/openai.yaml` 是 Codex 推荐的 UI 元数据;缺失不影响执行,但装好后会让 skill 列表展示更友好。
- 不需要额外修改 Codex 配置。安装或更新后,新开一个 Codex 会话即可加载最新 skill。
- 自动日报 / 周报只能用 Codex 桌面端的 local scheduled task。Codex CLI / IDE 没有 Scheduled
  管理界面；知识库是 Git 仓库也仍要选择 local，不能让默认 worktree 隔离掉报告写入。

---

## TraeWork / TraeCode 专项:发现与自动运行边界

宿主是 TraeWork、TraeCode 或 TRAE IDE 时，必须确保 `~/.trae-cn/skills/byteworker` 存在。
全局安装时它通常是指向 `SOURCE_DIR` 的符号链接；局部安装时它是直接 clone 出来的真实目录。
这是 TRAE 产品家族本机发现 skill 的目录；不要只装进 `~/.agents/skills`、
`~/.openclaw/skills`、`~/.claude/skills` 或 workspace 内目录来期待 TraeWork 自动发现。

安装或更新后，新开一个 TraeWork / TraeCode 会话让 skill 列表刷新。

- **TraeWork 桌面端**支持本地“自动化”任务。若当前任务涉及 Dreaming 或自动日报 / 周报，
  还要按第 6 步把**知识库绝对路径**加入 TraeWork 当前项目的工作目录；skill 安装目录只负责
  发现 `SKILL.md`，不会授予 TraeWork Sandbox 读写 KB 的权限。
- **TraeCode / TRAE IDE**没有可用的本地定时任务运行机制。安装完成后必须明确提醒用户：
  byteworker 可以手动使用，但无法在 TraeCode 自动创建日报 / 周报更新任务，也无法运行
  Dreaming。需要自动运行时请切换到 TraeWork 桌面端。

---

## OpenClaw 专项:确保对「所有 agent」都生效

OpenClaw 从 6 个来源自动发现 skill,且有 per-agent 的 skill 白名单 —— 装错位置、或撞了
配置,会出现「装了、却对某些 agent 不可见」。宿主是 OpenClaw 时,**在上面通用步骤之外
再做这 4 步**。

### a. 装在「所有 agent 可见」的位置

skill 的可见范围由它所在目录决定:

| 位置 | 谁能看到 |
|------|---------|
| `<workspace>/skills` · `<workspace>/.agents/skills` | 只有该 workspace 的 agent |
| `~/.agents/skills` · `~/.openclaw/skills` | 本机**所有** agent |

→ OpenClaw 的有效入口必须是 **`~/.openclaw/skills/byteworker`**。全局安装时这里通常是指向
`SOURCE_DIR` 的符号链接；局部安装且当前宿主就是 OpenClaw 时,这里可以是真实 clone。不要只装进
任何 `<workspace>` 目录。

### b. 全机去重 —— 同名 skill 只能有一份

OpenClaw 规则是「同名 skill,最高优先级来源胜出」。若 byteworker 同时存在于多个来源
(装过两次、或半成品残留),你会**静默跑到旧的那一份**,新装的被遮蔽。安装后检查这些
位置,**只保留 `~/.openclaw/skills/byteworker` 这个 OpenClaw 发现入口**,其余 OpenClaw 发现入口
(含悬空 symlink)删掉:
`~/.agents/skills/byteworker`、各 `<workspace>/skills/byteworker`、`<workspace>/.agents/skills/byteworker`。
全局安装时不要删除 `SOURCE_DIR`；它是真实代码源，`~/.openclaw/skills/byteworker` 应指向它。

### c. 排查 openclaw.json,别让配置盖掉 skill

配置文件:`~/.openclaw/openclaw.json`(JSON5 格式;可被环境变量 `OPENCLAW_CONFIG_PATH`
或 `--profile` 覆盖 —— 以实际生效的那个为准)。**文件不存在 = 全用默认值,本步跳过。**
存在则检查两处(**改动前先把你要改什么告诉用户**):

1. **`agents` 的 skill 白名单(最容易踩的坑)**。`agents.defaults.skills` 与
   `agents.list[].skills` 是**「替换」而非「合并」**的白名单 —— 只要设了某个白名单、其中
   又没有 `byteworker`,对应 agent 就看不到它(哪怕已正确安装)。处理:
   - 配置里**既无** `agents.defaults.skills`、**也无**任何 `agents.list[].skills` →
     skill 不受限,byteworker 对所有 agent 可见,**无需改**。
   - 设了 `agents.defaults.skills` → 往该数组加 `"byteworker"`。
   - 每个**显式写了自己 `skills` 数组**的 `agents.list[]` 条目 → 各自都要加 `"byteworker"`
     (替换语义,不会自动从 defaults 继承)。
   - 某 agent 是 `skills: []`(刻意锁死)→ **不要**硬塞;告诉用户「该 agent 被锁死、
     看不到 byteworker」,由用户定夺。
2. **`skills.entries` 里的 byteworker 残留**。byteworker **不需要任何 `skills.entries`
   配置** —— 自动发现 + 默认启用即可工作(`skills.entries` 只配置已发现的 skill,不注册
   skill)。只需排残留:若已存在 `skills.entries.byteworker` 块(或 SKILL.md 用
   `metadata.openclaw.skillKey` 指定的那个 key),确认它**没有** `enabled: false`、也没有
   指错的 env/config;若是上次失败安装留下的坏块,**整块删掉**。**不要**新增
   `skills.entries.byteworker`,除非确有 env / apiKey 要注入。

改 `openclaw.json` 注意:**直接改那个真实文件**,不要把它换成符号链接 —— OpenClaw 的
原子写会破坏符号链接式 config。它是 JSON5,逗号 / 括号别写坏。

### d. 让改动生效

OpenClaw 默认监视 `SKILL.md` 变更自动刷新。装完、改完配置后,保险起见**重启一次
OpenClaw 或新开 session**,并确认每个 agent 都能列出 / 调用 byteworker。

---

## 沙箱与云环境

- `git clone` 或 `npm install` 失败时，先确认沙箱能否访问外网。可在有网络的环境完成安装，
  为沙箱开放网络，或预置有有效 remote 的克隆。
- 知识库要放在跨会话保留且私密的目录，例如显式挂载的持久卷。沙箱重建可能清除临时目录中的资料。
- `bin/browse.sh` 需要能启动本地服务并打开浏览器。云端会话可以用 `/byteworker search` 或直接提问。

---

## 人工安装

先选择一种方式。

### 全局安装：本机多个工具共用

```bash
# 1. clone 一份真实仓库到固定源目录；也可以把 SOURCE_DIR 改成你指定的位置
SOURCE_DIR=~/byteworker
git clone https://github.com/ranjiao/byteworker.git "$SOURCE_DIR"

# 2. 自动检测本机已有且支持的 agent 环境，并链接到检测到的 skills 目录。
#    如果某个工具未被检测到但你确认要安装，手动把它的 skills 目录追加到 LINK_DIRS。
LINK_DIRS=()
[ -d "$HOME/.trae-cn" ] && LINK_DIRS+=("$HOME/.trae-cn/skills")
[ -d "$HOME/.claude" ] && LINK_DIRS+=("$HOME/.claude/skills")
[ -d "${CODEX_HOME:-$HOME/.codex}" ] && LINK_DIRS+=("${CODEX_HOME:-$HOME/.codex}/skills")
[ -d "$HOME/.openclaw" ] && LINK_DIRS+=("$HOME/.openclaw/skills")

printf '将安装软链接到以下 skills 目录:\n'
if [ "${#LINK_DIRS[@]}" -eq 0 ]; then
  printf '  未检测到已支持的 agent 环境；请手动把目标 skills 目录追加到 LINK_DIRS 后重试。\n'
  exit 1
fi
printf '  %s\n' "${LINK_DIRS[@]}"

for SKILLS_DIR in "${LINK_DIRS[@]}"; do
  mkdir -p "$SKILLS_DIR"
  ln -sfn "$SOURCE_DIR" "$SKILLS_DIR/byteworker"
done

# 3. 自查依赖
"$SOURCE_DIR/bin/check-deps.sh"

# 4. 可选:只读检查 Meego / Base / 风神授权状态(不会打开 OAuth)
"$SOURCE_DIR/bin/byteworker" source auth-status --source-type meego
"$SOURCE_DIR/bin/byteworker" source auth-status --source-type feishu_base
"$SOURCE_DIR/bin/byteworker" source auth-status --source-type aeolus
```

### 局部安装：只给当前工具使用

```bash
# 1. 按你当前运行环境设置 SKILLS_DIR(见上表)——
#      TraeWork / TraeCode / TRAE IDE: ~/.trae-cn/skills
#      Claude Code: ~/.claude/skills
#      Codex:       ${CODEX_HOME:-$HOME/.codex}/skills
#      OpenClaw:    ~/.openclaw/skills
SKILLS_DIR=~/.trae-cn/skills
mkdir -p "$SKILLS_DIR"

# 2. 直接 clone 到当前工具的 skills 目录；本机其它工具不会自动拥有这个 skill
git clone https://github.com/ranjiao/byteworker.git "$SKILLS_DIR/byteworker"

# 3. 自查依赖
"$SKILLS_DIR/byteworker/bin/check-deps.sh"

# 4. 可选:只读检查 Meego / Base / 风神授权状态(不会打开 OAuth)
"$SKILLS_DIR/byteworker/bin/byteworker" source auth-status --source-type meego
"$SKILLS_DIR/byteworker/bin/byteworker" source auth-status --source-type feishu_base
"$SKILLS_DIR/byteworker/bin/byteworker" source auth-status --source-type aeolus
```

若 `data.ready=false`，按第 5 步给需要的来源授权。暂时不用的来源可以跳过。

按第 6 步完成首次引导和自动报告设置。更新检查成功后，七天内会跳过重复检查。失败时按短周期指数退避重试。
代码更新后会运行 doctor，执行支持的兼容修复，并留下本地回滚记录。doctor 未完成时记录 pending，单独重试。
严重问题交给用户决定，warning/info 只显示简短摘要。固定版本环境可用 `BYTEWORKER_NO_AUTO_UPDATE=1` 停用自动更新。

## 全局安装细节

本机多个工具可以共用一份克隆。下面的命令将 `~/byteworker` 作为源，为检测到的工具创建链接。

```bash
# 1. clone 一次到固定位置(源)
git clone https://github.com/ranjiao/byteworker.git ~/byteworker

# 2. 自动检测并链接
LINK_DIRS=()
[ -d "$HOME/.trae-cn" ] && LINK_DIRS+=("$HOME/.trae-cn/skills")
[ -d "$HOME/.claude" ] && LINK_DIRS+=("$HOME/.claude/skills")
[ -d "${CODEX_HOME:-$HOME/.codex}" ] && LINK_DIRS+=("${CODEX_HOME:-$HOME/.codex}/skills")
[ -d "$HOME/.openclaw" ] && LINK_DIRS+=("$HOME/.openclaw/skills")

for SKILLS_DIR in "${LINK_DIRS[@]}"; do
  mkdir -p "$SKILLS_DIR"
  ln -sfn ~/byteworker "$SKILLS_DIR/byteworker"
done
```

如果 Claude Code 下已有一份克隆，也可以将其他工具链接到这份目录。

```bash
ln -sfn ~/.claude/skills/byteworker "${CODEX_HOME:-$HOME/.codex}/skills/byteworker"
ln -sfn ~/.claude/skills/byteworker ~/.openclaw/skills/byteworker
ln -sfn ~/.claude/skills/byteworker ~/.trae-cn/skills/byteworker
```

符号链接适用于工具的全局或托管 skills 目录。OpenClaw 的 workspace 级 skills 目录拒绝指向外部的链接，
需要直接克隆。自动更新会在源目录执行 fetch 和 fast-forward merge，因此源目录必须保留 `.git` 和 `origin`。
