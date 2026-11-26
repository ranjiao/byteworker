# 安装 Byteworker

可以把本页链接交给 AI 助手，让它安装 Byteworker 或修复旧安装。自己操作请看「人工安装」。

## 给 AI 助手的安装步骤

仓库地址是 `https://github.com/ranjiao/byteworker`，克隆地址是 `https://github.com/ranjiao/byteworker.git`。
用 Git 克隆安装，并保留 `.git` 和有效的 `origin`，供自动更新使用。

### 1. 选择安装范围并判定 skills 目录

先让用户明确选择安装范围。用户确认前，禁止克隆、删除残留或创建软链接。

- **全局安装**适合本机多个工具共用。优先复用已有的健康 Git 克隆，否则默认克隆到 `~/byteworker`。
  将真实克隆目录设为 `SOURCE_DIR` 和 `TARGET`。多个宿主能扫描同一个用户级目录时，只在该目录建立一个入口。
- **局部安装**只为当前工具设置入口。优先复用同机已有的克隆，再按下表选它实际扫描的目录。
  只有没有可用克隆时才直接克隆到 `$SKILLS_DIR/byteworker`。共享目录里的入口也会被其他扫描它的宿主看到。

全局安装的检测规则：

- 当前正在运行的宿主必须纳入覆盖范围，即使对应配置目录尚未存在。进入下表前先确认宿主、版本、实际 profile 和项目。
- TRAE IDE（含 TraeCode、SOLO）：检测到 `~/.trae-cn`，或当前会话运行在 TRAE IDE 中。
- Codex：检测到 `${CODEX_HOME:-$HOME/.codex}`，或当前环境是 Codex。
- Claude Code：检测到 `~/.claude`，或当前环境是 Claude Code。
- OpenClaw：检测到 `~/.openclaw`，或当前环境是 OpenClaw。
- 其它工具只有在用户明确给出其 skills 目录时才纳入。

| 宿主 | 用户级入口 | 还需排查的同名来源 |
|---|---|---|
| Codex | `~/.agents/skills` | 从当前目录到 Git 根目录的 `.agents/skills`，旧安装的 `${CODEX_HOME:-$HOME/.codex}/skills`，以及机器级入口。 [Codex 文档](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills) |
| Claude Code | `~/.claude/skills` | 项目或附加目录的 `.claude/skills`、托管技能，以及 Claude 已加载的插件或同步技能。 [Claude Code 文档](https://code.claude.com/docs/en/skills#choose-where-skills-load) |
| TRAE IDE | `~/.trae-cn/skills` | `<workspace>/.trae/skills`，以及项目已启用导入的 `<workspace>/.agents/skills`。 [TRAE IDE 文档](https://docs.trae.cn/ide_skills) |
| OpenClaw | 默认状态目录优先用 `~/.agents/skills`。使用独立 state/profile 或当前版本只发现托管入口时，选其实际 `<state-dir>/skills` | 有效 workspace 的 `skills` 和 `.agents/skills`、托管目录与已启用的额外来源。 [OpenClaw 文档](https://docs.openclaw.ai/tools/skills#loading-order) |
| 其它 | 用该宿主已确认的用户级发现目录。支持 `~/.agents/skills` 时复用共享入口 | 查对应项目目录、插件或显式配置的额外路径 |

先列出每个宿主会扫描的入口及其解析后的真实目录。优先用 `~/.agents/skills` 覆盖能实际读取它的宿主，
再为尚未覆盖的宿主选择最少的专用入口。已有共享入口能覆盖当前宿主时，直接复用。多个链接即使都指向
同一克隆，同一宿主也可能发现重复技能。完成去重后，每个目标宿主的有效技能清单应只出现一个 `byteworker`。

### 2. 检查旧安装

检查上一步发现的 Byteworker 入口。比较真实目录、Git 远端和未提交改动。遇到多份独立克隆时，先让
用户确认保留哪一份，并确认各自 `.kbconfig` 的知识库指向和需要保留的本地配置。指向不同知识库的安装
仅在用户明确选择统一后合并。已有共享入口、插件或组织托管的技能保留给仍依赖它们的宿主。

- 已有合法克隆（包含自己的 `.git` 和 `SKILL.md`）时，在它的真实目录执行「同步旧安装」。若 `origin`
  缺失且已核实这是官方仓库的克隆，可补上 `https://github.com/ranjiao/byteworker.git`。
- 入口已指向选定克隆时直接复用。新增入口验证可用后，只清理确认冗余或悬空的符号链接本身。
  删去共享链接前，逐一确认其他宿主已通过仍保留的入口发现 Byteworker。
- 已占用路径是独立克隆或真实目录时，先确认归属、未提交或未推送的改动、`.kbconfig` 和其他本地文件。
  经用户同意后把待退役的目录移出发现路径并保留备份。若该目录是现有链接的源，先规划所有链接的迁移。
- 没有可复用克隆且 `TARGET` 空闲时，继续第 3 步。多个入口只更新一次实际克隆。

#### 同步旧安装

远端重写历史后，旧版自动更新会提示“版本已分叉”，即使工作区干净也会停在旧版。安装助手和手动更新都应在实际安装目录执行下面这段命令。先确认 `origin` 指向本页的官方仓库。使用过风神的安装请在同一终端保留 `OLD_HEAD`，供第 5 步恢复原地址。

```bash
OLD_HEAD=$(git -C "$TARGET" rev-parse HEAD)
(
  set -eu
  cd "${TARGET:?请先设置安装目录 TARGET}"
  test "$(git branch --show-current)" = master || { echo "请先核对当前分支"; exit 1; }
  changes=$(git status --porcelain)
  test -z "$changes" || { echo "请先保存安装目录的本地改动"; exit 1; }
  git fetch --force origin refs/heads/master:refs/remotes/origin/master
  if ! git merge-base --is-ancestor "$OLD_HEAD" origin/master; then
    # 分叉时，只自动迁移本机曾从 origin/master 拉取过的版本。
    test "$(git merge-base --fork-point origin/master "$OLD_HEAD")" = "$OLD_HEAD" ||
      { echo "无法确认当前提交来自官方 master，请先核对并保存本地提交"; exit 1; }
  fi
  git reset --keep origin/master
  test "$(git rev-parse HEAD)" = "$(git rev-parse origin/master)"
)
```

强制拉取刷新远端引用，`reset --keep` 将安装目录切换到该版本，同时保留被 Git 忽略的 `.kbconfig`。命令失败时停止安装，处理提示后重试；只有两个提交一致才继续第 3 步。已有风神来源的用户还须完成第 5 步的地址检查。

### 3. 克隆或复用仓库

第 2 步找到合法克隆时直接复用。只有 `TARGET` 为空闲路径且没有可复用克隆时才克隆。

```bash
mkdir -p "$(dirname "$TARGET")"
git clone https://github.com/ranjiao/byteworker.git "$TARGET"
```

clone 或更新后，给第 1 步选定且尚未覆盖的发现目录创建软链接。已有入口指向 `TARGET` 时跳过。
如有其他目录占用该名称，先按第 2 步处理。

```bash
mkdir -p "$SKILLS_DIR"
ln -s "$TARGET" "$SKILLS_DIR/byteworker"
```

新开会话，确认每个目标宿主加载的是选定的 Byteworker，并在该安装目录运行 `bin/byteworker --help`。
如果发现多个同名入口，按第 2 步整理后再验证。

- 缺少 `git` 时先安装。macOS 用 `brew install git`，Debian/Ubuntu 用 `sudo apt install git`。
- 克隆遇到网络错误时，说明实际错误，并按「沙箱与云环境」排查。保留 Git 克隆的安装方式，自动更新依赖有效的 `origin`。
- 迁移 `.kbconfig` 时保留已确认的知识库指向，不覆盖另一份有效配置。

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

命令返回 `status=success` 后，看 `data.ready` 判断授权是否就绪。已启用的风神来源若报地址配置错误，直接按下方步骤修复。首次启用来源时先询问用户。

> 是否现在启用 Meego / 多维表格 / 风神定期摄取？它们需要用户授权。可以选择任意来源，
> 或“稍后再说”；跳过不影响 byteworker 其它能力。

只处理已启用或本次选中的来源。

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
- **风神地址配置与旧版修复**。本次启用风神、已有风神定期来源或凭据的安装，都要检查生效的地址文件。默认路径为 `~/.config/byteworker/aeolus-endpoints.json`；设有 `BYTEWORKER_AEOLUS_CONFIG` 时使用它指定的路径。若上面的检查返回 `SOURCE_CONFIG_INVALID`（`AEOLUS_ENDPOINTS_INVALID`），先修复地址文件，再检查授权。

  1. 从用户已确认的风神看板链接取 HTTPS 根地址（含自定义端口），填入 `dashboard_origin`。仅在使用 ByteCloud JWT 时，另填可信的 Passport 交换地址 `passport_endpoint`。旧安装可从第 2 步保存的 `OLD_HEAD` 对应的 `lib/aeolus_client.py` 中，在本机读取常量 `CN_HOSTS` 和 `CN_TITAN_PASSPORT_ENDPOINT` 恢复原值。若只有示例域名或无法确认地址，向用户索取实际值。安装助手应在本机将值直接写入配置，避免将地址输出到会话或日志。
  2. 文件缺失时，在仓库和知识库之外创建父目录及 JSON 文件，设置文件权限为 `600`。文件存在时保留有效字段，只修复缺失、格式错误或与看板站点不符的值。既有凭据继续使用原来的 `aeolus-auth.json`。下例只展示字段结构，两个示例地址都需要换成实际值，非 JWT 用户可省略第二项。

  ```json
  {
    "dashboard_origin": "https://dashboard.example.test",
    "passport_endpoint": "https://passport.example.test/example/exchange"
  }
  ```

  3. 在运行 skill 的同一环境执行 `"$TARGET/bin/byteworker" source auth-status --source-type aeolus`。以 `data.ready=true` 确认地址和凭据均可用。仍报地址错误时核对实际生效的配置路径及字段；提示缺少凭据或权限时按下一条处理。

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
- TRAE IDE 支持手动运行 Byteworker，跳过以下自动报告设置。有原生本地定时能力的宿主在初始化后读取
  `"$TARGET/references/report-scheduling.md"`，通过机器协议检查：

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
  - **其它宿主**：没有经核实的原生定时能力时，只展示 prompt 和缺口；不要擅自改用系统
    cron / launchd。
- 若用户选择“稍后再说”,明确告诉他:第一次摄取 Meego / Base / 风神时 skill 会再次给出同样的
  登录或最小 scope 授权引导；以后说“设置自动报告”可重新进入定时任务配置。
- 提醒用户选择持久、私密的知识库目录。沙箱临时目录可能被回收。
- 说明安装和首次设置结果。列出已创建的任务、绝对执行时间、运行环境和首次验证结果。
  任务未创建或验证失败时说明实际状态。

---

## Codex 专项:确保被自动发现

Codex 优先用 `~/.agents/skills/byteworker`。确认新会话能从共享入口加载后，再清理旧 Codex 目录里
已确认冗余的链接。项目和机器级目录也可能出现同名技能，按第 1 步核查。

本仓库的 Codex 入口是 `SKILL.md`。

- `SKILL.md` frontmatter 只保留 `name` 与 `description`,这是 Codex 的触发依据。
- `agents/openai.yaml` 是 Codex 推荐的 UI 元数据;缺失不影响执行,但装好后会让 skill 列表展示更友好。
- 不需要额外修改 Codex 配置。安装或更新后,新开一个 Codex 会话即可加载最新 skill。
- 自动日报 / 周报只能用 Codex 桌面端的 local scheduled task。Codex CLI / IDE 没有 Scheduled
  管理界面；知识库是 Git 仓库也仍要选择 local，不能让默认 worktree 隔离掉报告写入。

---

## TRAE IDE 安装

在 TRAE IDE、TraeCode 或 IDE 内置 SOLO 中，安装到 `~/.trae-cn/skills/byteworker`。
全局安装时它通常是指向 `SOURCE_DIR` 的符号链接；局部安装时它是直接 clone 出来的真实目录。
安装或更新后，新开一个 IDE 会话刷新 skill 列表。TRAE IDE 支持手动调用 Byteworker。
自动日报、周报和 Dreaming 后台任务需要在支持本地调度的宿主中打开同一知识库来设置。

---

## OpenClaw 安装检查

OpenClaw 按实际 state、workspace 和 agent 白名单发现技能。确认用于日常对话和定时任务的 profile。

### a. 装在「所有 agent 可见」的位置

默认 state 使用 `~/.agents/skills/byteworker` 和已读取该路径的宿主共享。独立 state 下使用其实际
`<state-dir>/skills/byteworker`，指向同一个克隆。按目标 agent 的清单验证可见性。

### b. 检查同名覆盖

OpenClaw 会在同名技能中选择优先级最高的一项。workspace 的 `skills` 和 `.agents/skills` 高于用户级
共享入口，默认 state 下用户级共享入口高于托管入口。若出现旧版本，检查有效 workspace、用户目录、
托管目录和额外来源，按第 2 步保留仍需使用的入口。 [OpenClaw 加载顺序](https://docs.openclaw.ai/tools/skills#loading-order)

### c. 排查 openclaw.json

如果路径正确，但目标 agent 看不到 Byteworker，检查实际 profile 或 `OPENCLAW_CONFIG_PATH` 所用的配置。

1. `agents.defaults.skills` 是默认白名单。显式的 `agents.entries.<agentId>.skills` 覆盖默认值，旧版用
   `agents.list[].skills`。只有目标 agent 的有效白名单需要启用 Byteworker 时，才经用户确认加入
   `byteworker`。空数组表示该 agent 禁用所有技能。 [OpenClaw 白名单](https://docs.openclaw.ai/tools/skills#agent-allowlists) · [旧字段迁移](https://docs.openclaw.ai/gateway/doctor/config-migrations#agent-roster-migration)
2. `skills.entries.byteworker` 用于覆写已发现的技能。检查已有条目是否显式禁用。默认发现不需要创建该条目。 [OpenClaw 技能配置](https://docs.openclaw.ai/tools/skills#config-overrides)

### d. 让改动生效

装完或改完配置后新开会话，确认目标 agent 能列出并调用预期的 Byteworker。

---

## 沙箱与云环境

- `git clone` 或 `npm install` 失败时，先确认沙箱能否访问外网。可在有网络的环境完成安装，
  为沙箱开放网络，或预置有有效 remote 的克隆。
- 知识库要放在跨会话保留且私密的目录，例如显式挂载的持久卷。沙箱重建可能清除临时目录中的资料。
- `bin/browse.sh` 需要能启动本地服务并打开浏览器。云端会话可以用 `/byteworker search` 或直接提问。

---

## 人工安装

已有克隆请先将 `TARGET` 设为实际安装目录，按[同步旧安装](#同步旧安装)更新；已使用风神的用户接着按[第 5 步](#5-询问可选来源授权)修复本机地址配置。
先按第 1、2 步选定每个宿主需要的入口并检查重复项，再选下面的安装方式。

### 全局安装：本机多个工具共用

下面以 Codex 和默认 state 的 OpenClaw 共用一个入口为例。按实际需要启用专用目录。

```bash
(
  set -eu
  SOURCE_DIR="$HOME/byteworker" # 或已核实的现有克隆的绝对路径
  LINK_DIRS=("$HOME/.agents/skills")
  # LINK_DIRS+=("$HOME/.claude/skills")   # 需要 Claude Code
  # LINK_DIRS+=("$HOME/.trae-cn/skills")  # 需要 TRAE IDE
  # LINK_DIRS+=("$OPENCLAW_STATE_DIR/skills") # 需要独立 state 的 OpenClaw

  for SKILLS_DIR in "${LINK_DIRS[@]}"; do
    ENTRY="$SKILLS_DIR/byteworker"
    if [ -e "$ENTRY" ] || [ -L "$ENTRY" ]; then
      if [ ! "$ENTRY" -ef "$SOURCE_DIR" ]; then
        printf '入口已占用，请先检查 %s\n' "$ENTRY" >&2
        exit 1
      fi
    fi
  done

  if [ ! -e "$SOURCE_DIR" ] && [ ! -L "$SOURCE_DIR" ]; then
    mkdir -p "$(dirname "$SOURCE_DIR")"
    git clone https://github.com/ranjiao/byteworker.git "$SOURCE_DIR"
  fi
  test -e "$SOURCE_DIR/.git" && test -f "$SOURCE_DIR/SKILL.md" || exit 1
  SOURCE_DIR=$(cd "$SOURCE_DIR" && pwd -P)

  for SKILLS_DIR in "${LINK_DIRS[@]}"; do
    ENTRY="$SKILLS_DIR/byteworker"
    [ "$ENTRY" -ef "$SOURCE_DIR" ] && continue
    mkdir -p "$SKILLS_DIR"
    ln -s "$SOURCE_DIR" "$ENTRY"
  done
  "$SOURCE_DIR/bin/check-deps.sh"
)
```

### 局部安装：只为当前工具设置入口

已发现 Byteworker 时复用现有入口。已有可用克隆时，用上面的链接方式，只填写当前工具需要的目录。
下面适用于 TRAE IDE 的首次安装；其他宿主按第 1 步选择实际发现目录。

```bash
# 1. 按你当前运行环境设置 SKILLS_DIR(见上表)——
#      TRAE IDE:   ~/.trae-cn/skills
#      Claude Code: ~/.claude/skills
#      Codex:       ~/.agents/skills
#      OpenClaw:    ~/.agents/skills 或实际 state 下的 skills
SKILLS_DIR=~/.trae-cn/skills
mkdir -p "$SKILLS_DIR"

# 2. 直接 clone 到选定的 skills 目录
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

OpenClaw 的 workspace 级目录默认限制指向外部的链接。多宿主共用安装选用户级或托管入口，保留源克隆的
`.git` 和 `origin` 供更新使用。 [OpenClaw 路径限制](https://docs.openclaw.ai/tools/skills#security)
