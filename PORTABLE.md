# QiQi claw 便携部署指南 (PORTABLE.md)

## 概述

本项目是 QiQi claw v0.12.0 的完整便携版本。
复制到任何 Linux/WSL/macOS 电脑后，运行一条命令即可完成部署。

## 系统要求

- **Python >= 3.11** (需 python3.11-venv 或 python3.12-venv)
- **pip** (Python 包管理器)
- **git** (可选，影响技能自动更新)
- 操作系统: Linux / macOS / WSL2

## 部署步骤 (新电脑)

### 1. 复制项目

```bash
# 将整个 qiqiclaw 目录复制到目标电脑的任意位置
# 例如:
cp -r qiqiclaw ~/qiqiclaw
```

### 2. 运行安装脚本

```bash
cd ~/qiqiclaw
bash setup-portable.sh
```

脚本自动完成:
- 检测 Python 环境
- 创建虚拟环境 (.venv)
- 安装所有依赖
- 部署配置文件到 ~/.qiqiclaw/
- 部署 89 个技能
- 部署 SOUL.md 个性 + MEMORY.md 记忆

### 3. 配置 API 密钥

```bash
# 编辑 .env 文件
nano ~/.qiqiclaw/.env

# 填入你的 DeepSeek API 密钥:
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxx
```

### 4. 启动

```bash
# 激活虚拟环境
source .venv/bin/activate

# 启动 QiQi claw
qiqi

# 或运行自检
qiqi doctor
```

## 项目结构

```
qiqiclaw/
├── setup-portable.sh       ← 一键安装脚本
├── portable_config/        ← 便携配置包
│   ├── config.yaml         ← 模型/工具/终端等设置
│   ├── .env.example        ← API 密钥模板 (复制为 .env 使用)
│   ├── SOUL.md             ← AI 个性定义
│   ├── memories/MEMORY.md  ← 持久记忆
│   └── skills/             ← 89 个技能 (20 个类别)
├── pyproject.toml          ← Python 包定义
├── venv/                   ← 虚拟环境 (脚本自动创建)
├── run_agent.py            ← 核心 Agent 循环
├── cli.py                  ← 交互式 CLI
├── agent/                  ← Agent 内部模块
├── tools/                  ← 工具实现
├── gateway/                ← 消息平台网关
├── skills/                 ← 内置技能 (源码)
├── tests/                  ← 测试套件
└── ...
```

## 配置说明 (部署后)

部署后配置文件位于 `~/.qiqiclaw/`:

| 文件 | 用途 |
|------|------|
| `config.yaml` | 模型选择、工具集、终端后端、压缩等全部设置 |
| `.env` | API 密钥 (DEEPSEEK_API_KEY 等) |
| `SOUL.md` | 自定义 AI 个性 |
| `memories/MEMORY.md` | 持久记忆 (跨会话) |
| `skills/` | 已安装的技能 (89 个) |
| `sessions/` | 会话记录 |
| `state.db` | SQLite 会话数据库 |

## 当前配置概览

| 配置项 | 值 |
|--------|-----|
| 默认模型 | deepseek-v4-pro |
| 提供商 | DeepSeek |
| API 端点 | https://api.deepseek.com/v1 |
| 最大对话轮次 | 60 |
| 终端后端 | local |
| 终端超时 | 60 秒 |
| 压缩 | 启用 (阈值 65%, 目标 25%) |
| 记忆 | 启用 (上限 2200 字符) |
| TTS | Edge (免费) |
| STT | local (faster-whisper) |
| 个性 | kawaii |
| 版本 | v23 |

## 故障排除

### Python 版本不够

```bash
# Ubuntu/Debian
sudo apt update && sudo apt install python3.11 python3.11-venv

# macOS
brew install python@3.11
```

### 命令找不到

```bash
# 确保虚拟环境已激活
source .venv/bin/activate
which qiqi
```

### 技能未加载

```bash
# 首次运行会自动注册技能
# 或手动同步:
qiqi skills list
```

### DeepSeek API 连接失败

```bash
# 检查 API 密钥
grep DEEPSEEK ~/.qiqiclaw/.env

# 测试连接
qiqi doctor
```
