<p align="center">
  <img src="assets/banner.png" alt="QIQI-Claw" width="100%">
</p>

# QIQI-Claw ☤

<p align="center">
  <a href="https://github.com/xzly111/qiqiclaw#readme"><img src="https://img.shields.io/badge/Docs-QiQiClaw-FFD700?style=for-the-badge" alt="Documentation"></a>
  <a href="https://github.com/xzly111/qiqiclaw/issues"><img src="https://img.shields.io/badge/Support-Issues-5865F2?style=for-the-badge&logo=github&logoColor=white" alt="Issues"></a>
  <a href="https://github.com/xzly111/qiqiclaw/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License: MIT"></a>
  <a href="https://github.com/xzly111/qiqiclaw/releases"><img src="https://img.shields.io/badge/Releases-QiQiClaw-blueviolet?style=for-the-badge" alt="QiQiClaw Releases"></a>
  <a href="README.zh-CN.md"><img src="https://img.shields.io/badge/Lang-中文-red?style=for-the-badge" alt="中文"></a>
  <a href="README.ur-pk.md"><img src="https://img.shields.io/badge/Lang-اردو-green?style=for-the-badge" alt="اردو"></a>
</p>

**QIQI-Claw is a self-improving AI agent with a built-in learning loop.** It creates skills from experience, improves them during use, nudges itself to persist knowledge, searches its own past conversations, and builds a deepening model of who you are across sessions. Run it on a $5 VPS, a GPU cluster, or serverless infrastructure that costs nearly nothing when idle. It's not tied to your laptop — talk to it from Telegram while it works on a cloud VM.

Use any model you want — [Nous Portal](https://portal.nousresearch.com), [OpenRouter](https://openrouter.ai) (200+ models), [NovitaAI](https://novita.ai) (AI-native cloud for Model API, Agent Sandbox, and GPU Cloud), [NVIDIA NIM](https://build.nvidia.com) (Nemotron), [Xiaomi MiMo](https://platform.xiaomimimo.com), [z.ai/GLM](https://z.ai), [Kimi/Moonshot](https://platform.moonshot.ai), [MiniMax](https://www.minimax.io), [Hugging Face](https://huggingface.co), OpenAI, or your own endpoint. Switch with `qiqiclaw model` — no code changes, no lock-in.

<table>
<tr><td><b>A real terminal interface</b></td><td>Full TUI with multiline editing, slash-command autocomplete, conversation history, interrupt-and-redirect, and streaming tool output.</td></tr>
<tr><td><b>Lives where you do</b></td><td>Telegram, Discord, Slack, WhatsApp, Signal, and CLI — all from a single gateway process. Voice memo transcription, cross-platform conversation continuity.</td></tr>
<tr><td><b>A closed learning loop</b></td><td>Agent-curated memory with periodic nudges. Autonomous skill creation after complex tasks. Skills self-improve during use. FTS5 session search with LLM summarization for cross-session recall. <a href="https://github.com/plastic-labs/honcho">Honcho</a> dialectic user modeling. Compatible with the <a href="https://agentskills.io">agentskills.io</a> open standard.</td></tr>
<tr><td><b>Scheduled automations</b></td><td>Built-in cron scheduler with delivery to any platform. Daily reports, nightly backups, weekly audits — all in natural language, running unattended.</td></tr>
<tr><td><b>Delegates and parallelizes</b></td><td>Spawn isolated subagents for parallel workstreams. Write Python scripts that call tools via RPC, collapsing multi-step pipelines into zero-context-cost turns.</td></tr>
<tr><td><b>Runs anywhere, not just your laptop</b></td><td>Six terminal backends — local, Docker, SSH, Singularity, Modal, and Daytona. Daytona and Modal offer serverless persistence — your agent's environment hibernates when idle and wakes on demand, costing nearly nothing between sessions. Run it on a $5 VPS or a GPU cluster.</td></tr>
<tr><td><b>Research-ready</b></td><td>Batch trajectory generation, trajectory compression for training the next generation of tool-calling models.</td></tr>
</table>

---

## Quick Install

### Linux, macOS, WSL2, Termux

```bash
curl -fsSL https://github.com/xzly111/qiqiclaw/install.sh | bash
```

### Windows (native, PowerShell)

> **Heads up:** Native Windows runs QIQI-Claw without WSL — CLI, gateway, TUI, and tools all work natively. If you'd rather use WSL2, the Linux/macOS one-liner above works there too. Found a bug? Please [file issues](https://github.com/xzly111/qiqiclaw/issues).

Run this in PowerShell:

```powershell
iex (irm https://github.com/xzly111/qiqiclaw/install.ps1)
```

The installer handles everything: uv, Python 3.11, Node.js, ripgrep, ffmpeg, **and a portable Git Bash** (MinGit, unpacked to `%LOCALAPPDATA%\hermes\git` — no admin required, completely isolated from any system Git install). QIQI-Claw uses this bundled Git Bash to run shell commands.

If you already have Git installed, the installer detects it and uses that instead. Otherwise a ~45MB MinGit download is all you need — it won't touch or interfere with any system Git.

> **Android / Termux:** The tested manual path is documented in the [Termux guide](https://github.com/xzly111/qiqiclaw#readmegetting-started/termux). On Termux, QIQI-Claw installs a curated `.[termux]` extra because the full `.[all]` extra currently pulls Android-incompatible voice dependencies.
>
> **Windows:** Native Windows is fully supported — the PowerShell one-liner above installs everything. If you'd rather use WSL2, the Linux command works there too. Native Windows install lives under `%LOCALAPPDATA%\hermes`; WSL2 installs under `~/.hermes` as on Linux. The only QIQI-Claw feature that currently needs WSL2 specifically is the browser-based dashboard chat pane (it uses a POSIX PTY — classic CLI and gateway both run natively).

After installation:

```bash
source ~/.bashrc    # reload shell (or: source ~/.zshrc)
qiqiclaw            # start chatting!
```

---

## Getting Started

```bash
qiqiclaw              # Interactive CLI — start a conversation
qiqiclaw model        # Choose your LLM provider and model
qiqiclaw tools        # Configure which tools are enabled
qiqiclaw config set   # Set individual config values
qiqiclaw gateway      # Start the messaging gateway (Telegram, Discord, etc.)
qiqiclaw setup        # Run the full setup wizard (configures everything at once)
qiqiclaw claw migrate # Migrate from OpenClaw (if coming from OpenClaw)
qiqiclaw update       # Update to the latest version
qiqiclaw doctor       # Diagnose any issues
```

📖 **[Full documentation →](https://github.com/xzly111/qiqiclaw#readme)**

---

## Skip the API-key collection — Nous Portal

QIQI-Claw works with whatever provider you want — that's not changing. But if you'd rather not collect five separate API keys for the model, web search, image generation, TTS, and a cloud browser, **[Nous Portal](https://portal.nousresearch.com)** covers all of them under one subscription:

- **300+ models** — pick any of them with `/model <name>`
- **Tool Gateway** — web search (Firecrawl), image generation (FAL), text-to-speech (OpenAI), cloud browser (Browser Use), all routed through your sub. No extra accounts.

One command from a fresh install:

```bash
qiqiclaw setup
```

That runs the setup wizard so you can choose your provider and turn on the Tool Gateway. Check the active services any time with `qiqiclaw status`. Full details on the [Tool Gateway docs page](https://github.com/xzly111/qiqiclaw#readmeuser-guide/features/tool-gateway).

You can still bring your own keys per-tool whenever you want — the gateway is per-backend, not all-or-nothing.

---

## CLI vs Messaging Quick Reference

QIQI-Claw has two entry points: start the terminal UI with `qiqiclaw`, or run the gateway and talk to it from Telegram, Discord, Slack, WhatsApp, Signal, or Email. Once you're in a conversation, many slash commands are shared across both interfaces.

| Action                         | CLI                                           | Messaging platforms                                                              |
| ------------------------------ | --------------------------------------------- | -------------------------------------------------------------------------------- |
| Start chatting                 | `qiqiclaw`                                    | Run `qiqiclaw gateway setup` + `qiqiclaw gateway start`, then send the bot a message |
| Start fresh conversation       | `/new` or `/reset`                            | `/new` or `/reset`                                                               |
| Change model                   | `/model [provider:model]`                     | `/model [provider:model]`                                                        |
| Set a personality              | `/personality [name]`                         | `/personality [name]`                                                            |
| Retry or undo the last turn    | `/retry`, `/undo`                             | `/retry`, `/undo`                                                                |
| Compress context / check usage | `/compress`, `/usage`, `/insights [--days N]` | `/compress`, `/usage`, `/insights [days]`                                        |
| Browse skills                  | `/skills` or `/<skill-name>`                  | `/<skill-name>`                                                                  |
| Interrupt current work         | `Ctrl+C` or send a new message                | `/stop` or send a new message                                                    |
| Platform-specific status       | `/platforms`                                  | `/status`, `/sethome`                                                            |

For the full command lists, see the [CLI guide](https://github.com/xzly111/qiqiclaw#readmeuser-guide/cli) and the [Messaging Gateway guide](https://github.com/xzly111/qiqiclaw#readmeuser-guide/messaging).

---

## Documentation

All documentation lives at **[github.com/xzly111/qiqiclaw/docs](https://github.com/xzly111/qiqiclaw#readme)**:

| Section                                                                                             | What's Covered                                             |
| --------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| [Quickstart](https://github.com/xzly111/qiqiclaw#readmegetting-started/quickstart)                 | Install → setup → first conversation in 2 minutes          |
| [CLI Usage](https://github.com/xzly111/qiqiclaw#readmeuser-guide/cli)                              | Commands, keybindings, personalities, sessions             |
| [Configuration](https://github.com/xzly111/qiqiclaw#readmeuser-guide/configuration)                | Config file, providers, models, all options                |
| [Messaging Gateway](https://github.com/xzly111/qiqiclaw#readmeuser-guide/messaging)                | Telegram, Discord, Slack, WhatsApp, Signal, Home Assistant |
| [Security](https://github.com/xzly111/qiqiclaw#readmeuser-guide/security)                          | Command approval, DM pairing, container isolation          |
| [Tools & Toolsets](https://github.com/xzly111/qiqiclaw#readmeuser-guide/features/tools)            | 40+ tools, toolset system, terminal backends               |
| [Skills System](https://github.com/xzly111/qiqiclaw#readmeuser-guide/features/skills)              | Procedural memory, Skills Hub, creating skills             |
| [Memory](https://github.com/xzly111/qiqiclaw#readmeuser-guide/features/memory)                     | Persistent memory, user profiles, best practices           |
| [MCP Integration](https://github.com/xzly111/qiqiclaw#readmeuser-guide/features/mcp)               | Connect any MCP server for extended capabilities           |
| [Cron Scheduling](https://github.com/xzly111/qiqiclaw#readmeuser-guide/features/cron)              | Scheduled tasks with platform delivery                     |
| [Context Files](https://github.com/xzly111/qiqiclaw#readmeuser-guide/features/context-files)       | Project context that shapes every conversation             |
| [Architecture](https://github.com/xzly111/qiqiclaw#readmedeveloper-guide/architecture)             | Project structure, agent loop, key classes                 |
| [Contributing](https://github.com/xzly111/qiqiclaw#readmedeveloper-guide/contributing)             | Development setup, PR process, code style                  |
| [CLI Reference](https://github.com/xzly111/qiqiclaw#readmereference/cli-commands)                  | All commands and flags                                     |
| [Environment Variables](https://github.com/xzly111/qiqiclaw#readmereference/environment-variables) | Complete env var reference                                 |

---

## Migrating from OpenClaw

If you're coming from OpenClaw, QIQI-Claw can automatically import your settings, memories, skills, and API keys.

**During first-time setup:** The setup wizard (`qiqiclaw setup`) automatically detects `~/.openclaw` and offers to migrate before configuration begins.

**Anytime after install:**

```bash
qiqiclaw claw migrate              # Interactive migration (full preset)
qiqiclaw claw migrate --dry-run    # Preview what would be migrated
qiqiclaw claw migrate --preset user-data   # Migrate without secrets
qiqiclaw claw migrate --overwrite  # Overwrite existing conflicts
```

What gets imported:

- **SOUL.md** — persona file
- **Memories** — MEMORY.md and USER.md entries
- **Skills** — user-created skills → `~/.hermes/skills/openclaw-imports/`
- **Command allowlist** — approval patterns
- **Messaging settings** — platform configs, allowed users, working directory
- **API keys** — allowlisted secrets (Telegram, OpenRouter, OpenAI, Anthropic, ElevenLabs)
- **TTS assets** — workspace audio files
- **Workspace instructions** — AGENTS.md (with `--workspace-target`)

See `qiqiclaw claw migrate --help` for all options, or use the `openclaw-migration` skill for an interactive agent-guided migration with dry-run previews.

---

## Contributing

We welcome contributions! See the [Contributing Guide](https://github.com/xzly111/qiqiclaw#readmedeveloper-guide/contributing) for development setup, code style, and PR process.

Quick start for contributors — clone and go with `setup-qiqiclaw.sh`:

```bash
git clone https://github.com/xzly111/qiqiclaw.git
cd qiqiclaw
./setup-qiqiclaw.sh   # installs uv, creates venv, installs .[all], symlinks ~/.local/bin/qiqiclaw
./qiqiclaw            # auto-detects the venv, no need to `source` first
```

Manual path (equivalent to the above):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv .venv --python 3.11
source .venv/bin/activate
uv pip install -e ".[all,dev]"
scripts/run_tests.sh
```

---

## Community

- 📚 [Skills Hub](https://agentskills.io)
- 🐛 [Issues](https://github.com/xzly111/qiqiclaw/issues)
- 🔌 [computer-use-linux](https://github.com/avifenesh/computer-use-linux) — Linux desktop-control MCP server for QIQI-Claw and other MCP hosts, with AT-SPI accessibility trees, Wayland/X11 input, screenshots, and compositor window targeting.
- 🔌 [QiQiClawClaw](https://github.com/xzly111/qiqiclaw) — Community WeChat bridge notes live with the QIQI-Claw project.

---

## License

MIT — see [LICENSE](LICENSE).

Maintained as QIQI-Claw.
