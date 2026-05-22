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
  `origin` remote,自动更新会永久静默失效。
- 若第 2 步保存过 `.kbconfig`,现在放回 `TARGET/.kbconfig`。

### 4. 检查依赖

```bash
"$TARGET/bin/check-deps.sh"
```

按退出码处理:
- **Tier 1**(`git` / `jq` / `bash`)缺失 → 帮用户装上(macOS `brew`,Linux `apt`)。
- **Tier 2**(`lark-cli` + `lark-*` skills)缺失 → 摄取飞书内容才需要,可稍后补。
  按[飞书 CLI 官方安装指南](https://open.feishu.cn/document/no_class/mcp-archive/feishu-cli-installation-guide.md)
  装 `lark-cli` 与 `lark-doc / minutes / vc / im / calendar / contact` 等 skill,并 `lark-cli auth login` 登录飞书。

### 5. 验证并收尾

- 确认 `"$TARGET/SKILL.md"` 存在。
- 告诉用户装好了 —— skill 首次使用时会问「知识库数据目录放在哪」。
- **提醒用户**:知识库数据目录要选一个**持久、私密**的路径,别放进会被回收的
  沙箱临时目录(原因见下)。

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

越来越多平台在托管沙箱 / 云虚拟机里跑 agent(如 OpenClaw 的 Docker / SSH 沙箱)。
这类环境有两个坑,安装前要心里有数:

1. **默认无外网**。沙箱常默认禁止出网 —— `git clone`、`npm install`(装 lark-cli)都会失败。
   解决:在有外网的环境安装;或为沙箱开放出网;或预置好 skill 目录。
   不要用没有 remote 的本地仓库来凑数 —— 自动更新会因此永久失效。
2. **文件系统可能是临时的**。沙箱重建后写入会丢失。
   - skill 本身丢了可重装,问题不大。
   - **知识库数据目录绝不能放临时盘** —— 它是你的真实知识资产。务必选一个跨会话
     持久、且私密的路径(沙箱里通常是显式挂载的持久卷)。

---

## 人工安装

```bash
# 1. 确定宿主 agent 的 skills 目录(见上表)
SKILLS_DIR=~/.claude/skills          # OpenClaw 用 ~/.openclaw/skills
mkdir -p "$SKILLS_DIR"

# 2. 直接 clone 进去
git clone https://github.com/ranjiao/byteworker.git "$SKILLS_DIR/byteworker"

# 3. 自查依赖
"$SKILLS_DIR/byteworker/bin/check-deps.sh"
```

之后 skill 每周静默从 GitHub 自动更新(`bin/update-check.sh`)。

## 多个 agent 共用一份代码

只想维护一份代码、供多个 agent 使用:把仓库 clone 到固定位置(如 `~/byteworker`),
再 symlink 进各 agent 的 skills 目录:

```bash
git clone https://github.com/ranjiao/byteworker.git ~/byteworker
ln -sfn ~/byteworker ~/.claude/skills/byteworker
```

注意:符号链接只在 agent 的**全局 / 托管** skills 目录可靠
(如 `~/.claude/skills`、`~/.openclaw/skills`)。OpenClaw 的 **workspace 级** skills
目录会拒绝指向目录之外的符号链接 —— 那里请直接 `git clone`。
