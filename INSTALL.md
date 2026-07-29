# 安装 byteworker skill

> 这是给 **AI 助手**的安装说明。把本文件的 URL 交给你的 AI 编码助手,让它照做即可。
> 人工安装见末尾「人工安装」。

byteworker 是一个 agent skill —— 一个含 `SKILL.md` 的目录。安装 = 把这个目录放到
**宿主 agent 能发现 skill 的位置**,并装好依赖。本文件让 agent 一步步完成,并且
**能修复之前没装好的残留**(可重复运行,幂等)。

---

## 给 AI 助手:请按以下步骤执行

### 0. 基本信息

- 仓库:`https://github.com/ranjiao/byteworker` · git URL:`https://github.com/ranjiao/byteworker.git`
- 安装方式:把仓库 **`git clone` 直接进宿主 agent 的 skills 目录**,目录名 `byteworker`。
- **优先直接 clone,不要「clone 到别处再 symlink」** —— 某些 agent(见下)对
  workspace 级 skills 的符号链接有安全限制;直接 clone 一个真实目录在所有环境都稳,
  而且 `git clone` 会带上 `origin` remote,自动更新才能工作。

### 1. 判定宿主 agent 的 skills 目录

你最清楚自己运行在什么 agent 里。据此确定 `SKILLS_DIR`:

| 宿主 agent | skills 目录(SKILLS_DIR)|
|-----------|--------------------------|
| Codex | `${CODEX_HOME:-$HOME/.codex}/skills` |
| Claude Code | `~/.claude/skills` |
| OpenClaw | `~/.openclaw/skills` —— **装这里**(本机所有 agent 可见);装好后**务必**再做下面的「OpenClaw 专项」 |
| 其它 | 该 agent 发现 skill / `SKILL.md` 的目录;不确定就问用户 |

执行 `mkdir -p "$SKILLS_DIR"`。最终安装位置:`TARGET = $SKILLS_DIR/byteworker`。

### 2. 先修复历史残留(若有)

安装前检查 `TARGET` 现状,把之前没装好的残留处理掉 —— 这一步保证可重复运行:

- **`TARGET` 是符号链接**(旧式 symlink 安装)→ `rm "$TARGET"` 删掉链接本身
  (不动它指向的真实目录)。继续。
- **`TARGET` 是目录、且是合法 byteworker 克隆**(同时存在 `TARGET/.git` 与 `TARGET/SKILL.md`)
  → 已装过,更新即可:
  - 若 `git -C "$TARGET" remote get-url origin` 没有 origin →
    `git -C "$TARGET" remote add origin https://github.com/ranjiao/byteworker.git`
  - `git -C "$TARGET" pull --ff-only`(失败不致命,跳过)
  - 直接跳到第 4 步。
- **`TARGET` 是目录、但不是合法克隆**(缺 `.git` 或缺 `SKILL.md`,即半成品 / 空目录)
  → 残留。**先保住用户数据**:若存在 `TARGET/.kbconfig`,复制到临时处;
  然后 `rm -rf "$TARGET"`,继续第 3 步重装,装完把 `.kbconfig` 放回 `TARGET/`。
- **`TARGET` 不存在** → 干净安装,继续第 3 步。
- 顺带看旧式残留 `~/byteworker`:是合法克隆可作来源;是半成品则清掉,避免混淆。

### 3. 取得 byteworker

```bash
git clone https://github.com/ranjiao/byteworker.git "$TARGET"
```

- 没有 `git` → 先装(macOS:`brew install git`;Debian/Ubuntu:`sudo apt install git`)。
- `git clone` 报网络错误 → 你大概在**无外网的沙箱**里(见末尾「沙箱 / 云环境」)。
  把情况如实告诉用户。**绝不**用「`git init` + 手工拼文件」来绕过 —— 那样的仓库没有
  `origin` remote,自动更新只能持续报错和重试。
- 若第 2 步保存过 `.kbconfig`,现在放回 `TARGET/.kbconfig`。

### 4. 检查依赖

```bash
"$TARGET/bin/check-deps.sh"
```

按退出码处理:
- **Tier 1**(`git` / `jq` / `bash` / `python3 >= 3.9`)缺失 → 帮用户装上(macOS `brew`,Linux `apt`)。
- **Tier 2**(`lark-cli` + `meegle` + 对应 skills)缺失 → 摄取飞书内容才需要,可稍后补。
  按[飞书 CLI 官方安装指南](https://open.feishu.cn/document/no_class/mcp-archive/feishu-cli-installation-guide.md)
  装 `lark-cli` 与 `lark-doc / minutes / vc / im / calendar / contact / base` 等 skill。
  摄取 Meego 保存视图时另装 `meegle` 与 `meegle` skill。登录和最小授权放到下一步,
  不要在用户未选择前自动打开 OAuth。

### 5. 询问可选来源授权

依赖安装和“用户授权”是两件事。只对**已经安装对应 CLI**的来源运行下面的只读检查:

```bash
python3 "$TARGET/bin/byteworker-cli.py" source auth-status --source-type meego
python3 "$TARGET/bin/byteworker-cli.py" source auth-status --source-type feishu_base
```

`status=success` 只表示检查命令正常完成；必须看 `data.ready`。若两个来源都已
`ready=true`,无需重复授权。只要有一个未就绪,安装助手必须先问:

> 是否现在启用 Meego / 多维表格定期摄取？它们需要用户 OAuth。可以选择“两者都启用”、
> “只启用 Meego”、“只启用多维表格”或“稍后再说”；跳过不影响 byteworker 其它能力。

只为用户选中的来源继续:

- **Meego** 使用独立的 `meegle` OAuth。先从用户给的资源 URL 确定 host；没有 URL 就问
  `project.feishu.cn`、`meegle.com` 还是自定义域名。然后执行
  `meegle auth login --host <host>`，等待浏览器授权完成，再运行一次 `source auth-status`
  验证。不得替用户猜站点。
- **多维表格** 复用 `lark-cli` 用户身份，但另需 Base 摄取的 5 个最小只读 scope。
  以 `source auth-status` 返回的 `data.action.command` 为准发起 split-flow；当前命令形如:

  ```bash
  lark-cli auth login \
    --scope "base:app:read base:table:read base:field:read base:view:read base:record:read" \
    --no-wait --json
  ```

  若 `lark-cli` 尚未初始化,先运行 `lark-cli config init --new`。发起授权后,把返回的
  `verification_url` **原样**展示给用户,并运行
  `lark-cli auth qrcode "<verification_url>" --output "<cwd 下的相对 PNG 路径>"`
  展示二维码；本轮到此结束。用户回复已完成后,由安装助手执行
  `lark-cli auth login --device-code <本次流程返回的 device_code>` 收尾,再运行
  `source auth-status` 验证。URL / device code 只用于这一次进行中的授权,不得写入
  skill 仓库或知识库。

授权 scope 齐全仍不代表能读取每个具体资源。Base 的 `91403`、Meego 空间
Permission Denied 属于资源共享权限,应让所有者给当前用户开权限；不要重复登录或自动改用 bot。

### 6. 验证并收尾

- 确认 `"$TARGET/SKILL.md"` 存在。
- 告诉用户装好了 —— skill 首次使用时会问「知识库数据目录放在哪」。
- 若用户选择“稍后再说”,明确告诉他:第一次摄取 Meego / Base 时 skill 会再次给出同样的
  登录或最小 scope 授权引导。
- **提醒用户**:知识库数据目录要选一个**持久、私密**的路径,别放进会被回收的
  沙箱临时目录(原因见下)。

---

## Codex 专项:确保被自动发现

宿主是 Codex 时,按上表装进 `${CODEX_HOME:-$HOME/.codex}/skills/byteworker` 即可被自动发现。
本仓库的 Codex 兼容入口是 `SKILL.md`:

- `SKILL.md` frontmatter 只保留 `name` 与 `description`,这是 Codex 的触发依据。
- `agents/openai.yaml` 是 Codex 推荐的 UI 元数据;缺失不影响执行,但装好后会让 skill 列表展示更友好。
- 不需要额外修改 Codex 配置。安装或更新后,新开一个 Codex 会话即可加载最新 skill。

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

→ 要对所有 agent 生效,装进 **`~/.openclaw/skills/byteworker`**(本文件默认位置)。
不要装进任何 `<workspace>` 目录。

### b. 全机去重 —— 同名 skill 只能有一份

OpenClaw 规则是「同名 skill,最高优先级来源胜出」。若 byteworker 同时存在于多个来源
(装过两次、或半成品残留),你会**静默跑到旧的那一份**,新装的被遮蔽。安装后检查这些
位置,**只保留 `~/.openclaw/skills/byteworker` 一份**,其余(含悬空 symlink)删掉:
`~/.agents/skills/byteworker`、各 `<workspace>/skills/byteworker`、`<workspace>/.agents/skills/byteworker`。

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

## 沙箱 / 云环境注意事项

越来越多平台在托管沙箱 / 云虚拟机里跑 agent(如 Codex 托管环境、OpenClaw 的 Docker / SSH 沙箱)。
这类环境有几个坑,安装前要心里有数:

1. **默认无外网**。沙箱常默认禁止出网 —— `git clone`、`npm install`(装 lark-cli)都会失败。
   解决:在有外网的环境安装;或为沙箱开放出网;或预置好 skill 目录。
   不要用没有 remote 的本地仓库来凑数 —— 自动更新会持续报告失败并按短周期退避重试。
2. **文件系统可能是临时的**。沙箱重建后写入会丢失。
   - skill 本身丢了可重装,问题不大。
   - **知识库数据目录绝不能放临时盘** —— 它是你的真实知识资产。务必选一个跨会话
     持久、且私密的路径(沙箱里通常是显式挂载的持久卷)。
3. **`bin/browse.sh`(知识库可视化浏览)用不了**。它要起本地 web 服务 + 开浏览器;
   沙箱通常起不了服务、也没有浏览器 / 显示。云平台 / 沙箱 agent 用户:`browse.sh`
   不适用是**预期的**、不是故障 —— 浏览知识库直接用对话查询(`/byteworker search`)。

---

## 人工安装

```bash
# 1. 按你实际用的 agent 设 SKILLS_DIR(见上表)——
#      Claude Code: ~/.claude/skills
#      Codex:       ${CODEX_HOME:-$HOME/.codex}/skills
#      OpenClaw:    ~/.openclaw/skills
SKILLS_DIR=~/.claude/skills
mkdir -p "$SKILLS_DIR"

# 2. 直接 clone 进去
git clone https://github.com/ranjiao/byteworker.git "$SKILLS_DIR/byteworker"

# 3. 自查依赖
"$SKILLS_DIR/byteworker/bin/check-deps.sh"

# 4. 可选:只读检查 Meego / Base 授权状态(不会打开 OAuth)
python3 "$SKILLS_DIR/byteworker/bin/byteworker-cli.py" source auth-status --source-type meego
python3 "$SKILLS_DIR/byteworker/bin/byteworker-cli.py" source auth-status --source-type feishu_base
```

若 `data.ready=false`,按上面第 5 步的相应流程授权；不打算摄取该来源可直接跳过。

之后 skill 在成功检查后 7 天内静默跳过重复检查(`bin/update-check.sh`)；失败会按短周期指数
退避重试，不再把失败时间当作成功时间。代码确实更新后会自动运行 doctor，修复确定性低成本
兼容问题并创建知识库本地回滚提交；doctor 未完成会记录 pending 并单独重试。无法自动处理的
严重错误会请求用户决策，warning/info 只给简短摘要。固定版本环境可设置
`BYTEWORKER_NO_AUTO_UPDATE=1` 显式停用。

## 多个 agent 共用一份代码

只想维护一份代码、供多个 agent 使用:**任选一个位置作为"源"clone 一次**,其它 agent
的 skills 目录用 symlink 指过去。源放哪都可以(独立目录如 `~/byteworker`、或你最常用的
那个 agent 的 skills 目录都行)—— 关键是只 `git clone` 一次,其余都是 symlink。

示例:以独立目录 `~/byteworker` 作源,链接给三家 agent。**只跑你实际用的那些 agent 对应的行**:

```bash
# 1. clone 一次到固定位置(源)
git clone https://github.com/ranjiao/byteworker.git ~/byteworker

# 2. 把它链接进各 agent 的 skills 目录(用哪个就跑哪行)
ln -sfn ~/byteworker ~/.claude/skills/byteworker
ln -sfn ~/byteworker "${CODEX_HOME:-$HOME/.codex}/skills/byteworker"
ln -sfn ~/byteworker ~/.openclaw/skills/byteworker
```

也可以把源就放在某个 agent 的 skills 目录(例如已经 clone 在 `~/.claude/skills/byteworker`),
再把其它两家的 symlink 指过去 —— 方向不重要,**只要全机只有一份真实 clone**即可:

```bash
ln -sfn ~/.claude/skills/byteworker "${CODEX_HOME:-$HOME/.codex}/skills/byteworker"
ln -sfn ~/.claude/skills/byteworker ~/.openclaw/skills/byteworker
```

**注意**:
- 符号链接只在 agent 的**全局 / 托管** skills 目录可靠(`~/.claude/skills`、
  `${CODEX_HOME:-$HOME/.codex}/skills`、`~/.openclaw/skills`)。OpenClaw 的 **workspace 级**
  skills 目录会拒绝指向目录之外的符号链接 —— 那里请直接 `git clone`,不要 symlink。
- 共用一份后,**自动更新只在源那份生效**(`bin/update-check.sh` 在源目录执行 fetch +
  fast-forward merge，symlink 那几家直接看到更新)。所以源目录要是合法 clone(有 `.git` 与
  `origin` remote)。
