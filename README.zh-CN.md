# QiQiClaw Agent ☤

<p align="center">
  <a href="https://github.com/xzly111/qiqiclaw#readme"><img src="https://img.shields.io/badge/Docs-QiQiClaw-FFD700?style=for-the-badge" alt="Documentation"></a>
  <a href="https://github.com/xzly111/qiqiclaw/issues"><img src="https://img.shields.io/badge/Support-Issues-5865F2?style=for-the-badge&logo=github&logoColor=white" alt="Issues"></a>
  <a href="https://github.com/xzly111/qiqiclaw/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License: MIT"></a>
  <a href="https://github.com/xzly111/qiqiclaw/releases"><img src="https://img.shields.io/badge/Releases-QiQiClaw-blueviolet?style=for-the-badge" alt="QiQiClaw Releases"></a>
  <a href="README.md"><img src="https://img.shields.io/badge/Lang-English-lightgrey?style=for-the-badge" alt="English"></a>
  <a href="README.ur-pk.md"><img src="https://img.shields.io/badge/Lang-اردو-green?style=for-the-badge" alt="اردو"></a>
</p>

**QiQiClaw Agent 是面向桌面端、终端和消息平台的自进化 AI 代理运行时。** 它会从经验中创建技能，在使用中改进技能，持久化有用知识，搜索过往对话，并在跨会话中逐步理解你的偏好。你可以把它运行在本机、VPS、GPU 机器或长期运行的云端环境中，再通过桌面端、CLI、Telegram、Discord、Slack、WhatsApp、Signal 或 Email 与它对话。

支持你偏好的模型提供方——OpenRouter、OpenAI 兼容端点、Anthropic、DeepSeek、Qwen、Kimi/Moonshot、MiniMax、Hugging Face、本地模型或自定义网关。使用 `qiqiclaw model` 即可切换——无需改代码，无锁定。

<table>
<tr><td><b>真正的终端界面</b></td><td>完整的 TUI，支持多行编辑、斜杠命令自动补全、对话历史、中断重定向和流式工具输出。</td></tr>
<tr><td><b>随你所在</b></td><td>Telegram、Discord、Slack、WhatsApp、Signal 和 CLI——全部从单个网关进程运行。语音备忘录转写、跨平台对话连续性。</td></tr>
<tr><td><b>闭环学习</b></td><td>代理管理记忆并定期自我提醒。复杂任务后自动创建技能。技能在使用中自我改进。FTS5 会话搜索配合 LLM 摘要实现跨会话回溯。<a href="https://github.com/plastic-labs/honcho">Honcho</a> 辩证式用户建模。兼容 <a href="https://agentskills.io">agentskills.io</a> 开放标准。</td></tr>
<tr><td><b>定时自动化</b></td><td>内置 cron 调度器，支持向任何平台投递。日报、夜间备份、周审计——全部用自然语言描述，无人值守运行。</td></tr>
<tr><td><b>委派与并行</b></td><td>生成隔离子代理处理并行工作流。编写 Python 脚本通过 RPC 调用工具，将多步管道压缩为零上下文开销的轮次。</td></tr>
<tr><td><b>随处运行</b></td><td>六种终端后端——本地、Docker、SSH、Daytona、Singularity 和 Modal。Daytona 和 Modal 提供 Serverless 持久化——代理环境空闲时休眠、按需唤醒，空闲期间几乎零成本。$5 VPS 或 GPU 集群都能跑。</td></tr>
<tr><td><b>研究就绪</b></td><td>批量轨迹生成、轨迹压缩——用于训练下一代工具调用模型。</td></tr>
</table>

---

## 快速安装

```bash
curl -fsSL https://github.com/xzly111/qiqiclaw/install.sh | bash
```

支持 Linux、macOS、Windows 原生、WSL2 和 Android (Termux)。安装程序会自动处理平台特定的配置。

> **Android / Termux：** 已测试的手动安装路径请参考 [Termux 指南](https://github.com/xzly111/qiqiclaw#readmegetting-started/termux)。在 Termux 上，QIQI-Claw 会安装精选的 `.[termux]` 扩展，因为完整的 `.[all]` 扩展会拉取 Android 不兼容的语音依赖。
>
> **Windows：** 原生 Windows 已支持。可使用 PowerShell 安装脚本；如果你更偏好 WSL2，也可以在 WSL2 内运行 Linux/macOS 安装命令。

安装后：

```bash
source ~/.bashrc    # 重新加载 shell（或: source ~/.zshrc）
qiqiclaw            # 开始对话！
```

---

## 快速入门

```bash
qiqiclaw              # 交互式 CLI — 开始对话
qiqiclaw model        # 选择 LLM 提供商和模型
qiqiclaw tools        # 配置启用的工具
qiqiclaw config set   # 设置单个配置项
qiqiclaw gateway      # 启动消息网关（Telegram、Discord 等）
qiqiclaw setup        # 运行完整设置向导（一次性配置所有内容）
qiqiclaw claw migrate # 从 OpenClaw 迁移（如果来自 OpenClaw）
qiqiclaw update       # 更新到最新版本
qiqiclaw doctor       # 诊断问题
```

📖 **[完整文档 →](https://github.com/xzly111/qiqiclaw#readme)**

---

## 配置 API 提供方

QiQiClaw Agent 可以对接你选择的提供方组合。设置向导可以在同一流程里配置模型提供方、工具后端、API Key 和消息网关：

- **模型提供方** — OpenAI 兼容端点、OpenRouter、Anthropic、DeepSeek、Qwen、Kimi/Moonshot、MiniMax、本地模型等。
- **工具网关** — 网页搜索、图像生成、文本转语音、浏览器自动化和其他工具后端可独立配置。
- **消息网关** — 连接 Telegram、Discord、Slack、WhatsApp、Signal、Email 等平台。

全新安装时一条命令即可：

```bash
qiqiclaw setup
```

它会运行设置向导，方便你选择提供方、保存 API 凭据并启用工具网关。随时用 `qiqiclaw status` 查看当前服务状态。完整说明见 [Tool Gateway 文档](https://github.com/xzly111/qiqiclaw#readmeuser-guide/features/tool-gateway)。

你随时可以按工具单独切回自己的 API Key — Gateway 是按工具粒度生效的，不是一刀切。

---

## CLI 与消息平台 快速对照

QIQI-Claw 有两种入口：用 `qiqiclaw` 启动终端 UI，或运行网关从 Telegram、Discord、Slack、WhatsApp、Signal 或 Email 与之对话。进入对话后，许多斜杠命令在两种界面中通用。

| 操作 | CLI | 消息平台 |
|------|-----|----------|
| 开始对话 | `qiqiclaw` | 运行 `qiqiclaw gateway setup` + `qiqiclaw gateway start`，然后给机器人发消息 |
| 开始新对话 | `/new` 或 `/reset` | `/new` 或 `/reset` |
| 更换模型 | `/model [provider:model]` | `/model [provider:model]` |
| 设置人格 | `/personality [name]` | `/personality [name]` |
| 重试或撤销上一轮 | `/retry`、`/undo` | `/retry`、`/undo` |
| 压缩上下文 / 查看用量 | `/compress`、`/usage`、`/insights [--days N]` | `/compress`、`/usage`、`/insights [days]` |
| 浏览技能 | `/skills` 或 `/<skill-name>` | `/skills` 或 `/<skill-name>` |
| 中断当前工作 | `Ctrl+C` 或发送新消息 | `/stop` 或发送新消息 |
| 平台特定状态 | `/platforms` | `/status`、`/sethome` |

完整命令列表请参阅 [CLI 指南](https://github.com/xzly111/qiqiclaw#readmeuser-guide/cli) 和 [消息网关指南](https://github.com/xzly111/qiqiclaw#readmeuser-guide/messaging)。

---

## 文档

所有文档位于 **[github.com/xzly111/qiqiclaw/docs](https://github.com/xzly111/qiqiclaw#readme)**：

| 章节 | 内容 |
|------|------|
| [快速开始](https://github.com/xzly111/qiqiclaw#readmegetting-started/quickstart) | 安装 → 设置 → 2 分钟内开始首次对话 |
| [CLI 使用](https://github.com/xzly111/qiqiclaw#readmeuser-guide/cli) | 命令、快捷键、人格、会话 |
| [配置](https://github.com/xzly111/qiqiclaw#readmeuser-guide/configuration) | 配置文件、提供商、模型、所有选项 |
| [消息网关](https://github.com/xzly111/qiqiclaw#readmeuser-guide/messaging) | Telegram、Discord、Slack、WhatsApp、Signal、Home Assistant |
| [安全](https://github.com/xzly111/qiqiclaw#readmeuser-guide/security) | 命令审批、DM 配对、容器隔离 |
| [工具与工具集](https://github.com/xzly111/qiqiclaw#readmeuser-guide/features/tools) | 40+ 工具、工具集系统、终端后端 |
| [技能系统](https://github.com/xzly111/qiqiclaw#readmeuser-guide/features/skills) | 过程记忆、技能中心、创建技能 |
| [记忆](https://github.com/xzly111/qiqiclaw#readmeuser-guide/features/memory) | 持久记忆、用户画像、最佳实践 |
| [MCP 集成](https://github.com/xzly111/qiqiclaw#readmeuser-guide/features/mcp) | 连接任意 MCP 服务器扩展能力 |
| [定时调度](https://github.com/xzly111/qiqiclaw#readmeuser-guide/features/cron) | 定时任务与平台投递 |
| [上下文文件](https://github.com/xzly111/qiqiclaw#readmeuser-guide/features/context-files) | 影响每次对话的项目上下文 |
| [架构](https://github.com/xzly111/qiqiclaw#readmedeveloper-guide/architecture) | 项目结构、代理循环、关键类 |
| [贡献](https://github.com/xzly111/qiqiclaw#readmedeveloper-guide/contributing) | 开发设置、PR 流程、代码风格 |
| [CLI 参考](https://github.com/xzly111/qiqiclaw#readmereference/cli-commands) | 所有命令和标志 |
| [环境变量](https://github.com/xzly111/qiqiclaw#readmereference/environment-variables) | 完整环境变量参考 |

---

## 从 OpenClaw 迁移

如果你来自 OpenClaw，QIQI-Claw 可以自动导入你的设置、记忆、技能和 API 密钥。

**首次安装时：** 安装向导（`qiqiclaw setup`）会自动检测 `~/.openclaw` 并在配置开始前提供迁移选项。

**安装后任意时间：**

```bash
qiqiclaw claw migrate              # 交互式迁移（完整预设）
qiqiclaw claw migrate --dry-run    # 预览将要迁移的内容
qiqiclaw claw migrate --preset user-data   # 仅迁移用户数据，不含密钥
qiqiclaw claw migrate --overwrite  # 覆盖已有冲突
```

导入内容：
- **SOUL.md** — 人格文件
- **记忆** — MEMORY.md 和 USER.md 条目
- **技能** — 用户创建的技能 → `~/.hermes/skills/openclaw-imports/`
- **命令白名单** — 审批模式
- **消息设置** — 平台配置、允许用户、工作目录
- **API 密钥** — 白名单中的密钥（Telegram、OpenRouter、OpenAI、Anthropic、ElevenLabs）
- **TTS 资产** — 工作区音频文件
- **工作区指令** — AGENTS.md（使用 `--workspace-target`）

使用 `qiqiclaw claw migrate --help` 查看所有选项，或使用 `openclaw-migration` 技能进行交互式代理引导迁移（含干运行预览）。

---

## 贡献

欢迎贡献！请参阅 [贡献指南](https://github.com/xzly111/qiqiclaw#readmedeveloper-guide/contributing) 了解开发设置、代码风格和 PR 流程。

贡献者快速开始——克隆并使用 `setup-qiqiclaw.sh`：

```bash
git clone https://github.com/xzly111/qiqiclaw.git
cd qiqiclaw
./setup-qiqiclaw.sh   # 安装 uv、创建 venv、安装 .[all]、创建符号链接 ~/.local/bin/qiqiclaw
./qiqiclaw            # 自动检测 venv，无需先 source
```

手动安装（等效于上述命令）：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv venv --python 3.11
source venv/bin/activate
uv pip install -e ".[all,dev]"
python -m pytest tests/ -q
```

---

## 社区

- 📚 [技能中心](https://agentskills.io)
- 🐛 [问题反馈](https://github.com/xzly111/qiqiclaw/issues)
- 💡 [讨论区](https://github.com/xzly111/qiqiclaw/discussions)
- 🔌 [QiQiClawClaw](https://github.com/xzly111/qiqiclaw) — 社区微信桥接说明随 QIQI-Claw 项目维护。

---

## 许可证

MIT — 详见 [LICENSE](LICENSE)。

作为 QIQI-Claw 维护。
