# QiQiClaw - QIQI claw 汉化版本

<p align="center">
  <img src="assets/banner.png" alt="QiQiClaw" width="100%">
</p>

## 项目简介

QiQiClaw 是 QIQI-Claw 的中文化发行版本。这是一个功能强大的自主 AI 代理系统，支持多平台部署和多种 LLM 提供商。

### 主要特性

- ✅ **完整汉化**：已汉化 `hermes_cli/auth.py` 模块（158 处中文提示）
- 🤖 **多模型支持**：支持 20+ LLM 提供商（OpenRouter、Anthropic、OpenAI、DeepSeek 等）
- 💬 **跨平台消息网关**：支持 Telegram、Discord、Slack、WhatsApp、Signal 等
- 🧠 **持久化记忆**：跨会话记忆用户偏好和上下文
- 🔧 **技能系统**：可学习和改进的技能库
- ⏰ **定时任务**：内置 cron 调度器
- 🐳 **Docker 支持**：完整的容器化部署方案
- 🧪 **完整测试套件**：包含 3000+ 测试用例

## 快速开始

### 系统要求

- **Python**: 3.11 或更高版本
- **操作系统**: Linux、macOS、Windows 原生、WSL2
- **Git**: 用于克隆仓库

### 安装步骤

#### 方法 1: 使用安装脚本（推荐）

```bash
# 克隆仓库
git clone https://github.com/xzly111/qiqiclaw.git
cd qiqiclaw

# 运行安装脚本
curl -fsSL https://raw.githubusercontent.com/xzly111/qiqiclaw/main/scripts/install.sh | bash

# 重新加载 shell 配置
source ~/.bashrc  # 或 source ~/.zshrc

# 启动交互界面
qiqiclaw
```

#### 方法 2: 手动安装

```bash
# 克隆仓库
git clone https://github.com/xzly111/qiqiclaw.git
cd qiqiclaw

# 安装 uv（Python 包管理器）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 创建虚拟环境
uv venv venv --python 3.11

# 激活虚拟环境
source venv/bin/activate  # Linux/macOS
# 或
.\venv\Scripts\activate  # Windows

# 安装依赖
uv pip install -e ".[all]"

# 启动
./qiqiclaw
```

### 配置

```bash
# 运行设置向导
qiqiclaw setup

# 选择模型和提供商
qiqiclaw model

# 配置工具集
qiqiclaw tools

# 检查系统状态
qiqiclaw doctor
```

## 核心依赖

### 必需依赖

根据 `pyproject.toml`，项目核心依赖包括：

```
- Python >= 3.11
- openai >= 2.21.0
- anthropic >= 0.39.0
- httpx[socks] >= 0.28.1
- rich >= 14.3.3
- pydantic >= 2.12.5
- prompt-toolkit >= 3.0.48
- tiktoken >= 0.8.0
- python-dotenv >= 1.0.1
- pyyaml >= 6.0.2
- click >= 8.1.8
- aiofiles >= 24.1.0
- psutil >= 6.1.1
```

### 可选功能依赖

```bash
# 消息网关（Telegram、Discord、Slack 等）
pip install -e ".[messaging]"

# 语音识别支持
pip install -e ".[voice]"

# CLI 增强界面
pip install -e ".[cli]"

# Modal 部署支持
pip install -e ".[modal]"

# Daytona 环境支持
pip install -e ".[daytona]"

# 完整安装（包含所有功能）
pip install -e ".[all]"
```

## 使用指南

### 基本命令

```bash
# 启动交互式聊天
qiqiclaw

# 单次查询
qiqiclaw chat -q "你的问题"

# 查看配置
qiqiclaw config

# 编辑配置文件
qiqiclaw config edit

# 查看帮助
qiqiclaw --help
```

### 会话内命令

在交互式会话中可以使用以下命令：

```
/new          - 开始新会话
/reset        - 重置当前会话
/model        - 切换模型
/tools        - 管理工具
/skills       - 浏览技能
/help         - 显示帮助
/quit         - 退出
```

### 配置 API 密钥

编辑 `~/.hermes/.env` 文件添加你的 API 密钥：

```bash
# OpenRouter
OPENROUTER_API_KEY=your_key_here

# Anthropic
ANTHROPIC_API_KEY=your_key_here

# OpenAI
OPENAI_API_KEY=your_key_here

# DeepSeek
DEEPSEEK_API_KEY=your_key_here

# 其他提供商...
```

## 消息网关部署

### Telegram Bot 配置

```bash
# 运行网关设置向导
qiqiclaw gateway setup

# 选择 Telegram 并输入 Bot Token

# 启动网关
qiqiclaw gateway start

# 查看状态
qiqiclaw gateway status
```

### 支持的平台

- Telegram
- Discord
- Slack
- WhatsApp
- Signal
- Email
- SMS
- Matrix
- Mattermost
- Home Assistant
- 更多...

## Docker 部署

```bash
# 构建镜像
docker build -t qiqiclaw .

# 运行容器
docker run -it --rm \
  -v ~/.hermes:/root/.hermes \
  -e OPENROUTER_API_KEY=your_key \
  qiqiclaw

# 使用 docker-compose
docker-compose up -d
```

## 项目结构

```
qiqiclaw/
├── hermes_cli/          # CLI 命令和配置
│   ├── auth.py         # 认证模块（已汉化）
│   ├── config.py       # 配置管理
│   └── main.py         # CLI 入口
├── agent/              # 核心代理逻辑
├── tools/              # 工具集
├── gateway/            # 消息网关
│   └── platforms/      # 平台适配器
├── skills/             # 技能库
├── tests/              # 测试套件
├── docker/             # Docker 配置
├── website/            # 文档网站
├── pyproject.toml      # 项目配置和依赖
├── uv.lock            # 依赖锁定文件
└── README_CN.md        # 中文说明文档
```

## 汉化进度

### 已完成
- ✅ `hermes_cli/auth.py` - 认证模块（158 处汉化）
- ✅ 修复中文引号语法错误（3 处）
- ✅ 语法验证通过

### 待完成
- ⏳ CLI 主界面提示信息
- ⏳ 错误消息汉化
- ⏳ 帮助文档汉化
- ⏳ 配置向导汉化

## 测试

```bash
# 运行完整测试套件
python -m pytest tests/ -o 'addopts=' -q

# 运行特定模块测试
python -m pytest tests/tools/ -q

# 测试认证模块
python -m pytest tests/hermes_cli/test_auth.py -v
```

## 故障排除

### 常见问题

1. **Python 版本不兼容**
   ```bash
   python3 --version  # 确保 >= 3.11
   ```

2. **依赖安装失败**
   ```bash
   # 使用 uv 重新安装
   uv pip install -e ".[all]" --force-reinstall
   ```

3. **API 密钥未配置**
   ```bash
   # 检查环境变量
   qiqiclaw config env-path
   cat ~/.hermes/.env
   ```

4. **网关无法启动**
   ```bash
   # 查看日志
   tail -f ~/.hermes/logs/gateway.log
   
   # 重启网关
   qiqiclaw gateway restart
   ```

## 贡献指南

欢迎贡献代码和改进建议！

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 开启 Pull Request

### 汉化贡献

如果你想帮助汉化更多模块：

1. 选择一个未汉化的模块
2. 保持代码逻辑不变，只翻译用户可见的字符串
3. 注意中文引号问题（使用英文引号）
4. 运行 `python3 -m py_compile` 验证语法
5. 提交 PR

## 版本信息

- **项目版本**: 0.12.0
- **汉化日期**: 2026-05-01
- **基于**: QIQI-Claw
- **许可证**: MIT

## 相关链接

- **GitHub 仓库**: https://github.com/xzly111/qiqiclaw
- **项目文档**: https://github.com/xzly111/qiqiclaw#readme
- **问题反馈**: https://github.com/xzly111/qiqiclaw/issues

## 许可证

MIT License - 详见 [LICENSE](LICENSE) 文件

作为 QIQI-Claw 中文化发行版本维护。

---

**注意**: 本项目是 QIQI-Claw 的中文化版本，旨在为中文用户提供更好的使用体验。所有核心功能保持完整。
