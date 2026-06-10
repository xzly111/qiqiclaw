# QiQi Claw API 密钥配置指南

## 目录
1. [快速开始](#快速开始)
2. [推荐配置方案](#推荐配置方案)
3. [详细配置说明](#详细配置说明)
4. [常见问题](#常见问题)

## 快速开始

### 最小配置（新手推荐）

只需配置一个 LLM 提供商即可开始使用：

```bash
# 1. 复制配置文件
cp ~/.qiqiclaw/.env.example.cn ~/.qiqiclaw/.env

# 2. 编辑配置文件
nano ~/.qiqiclaw/.env

# 3. 添加 DeepSeek API 密钥（推荐 - 性价比高）
DEEPSEEK_API_KEY=your_deepseek_key_here

# 4. 运行设置向导
qiqiclaw setup

# 5. 开始使用
qiqiclaw
```

### 获取 DeepSeek API 密钥

1. 访问 https://platform.deepseek.com/
2. 注册账号并登录
3. 进入 API Keys 页面
4. 创建新的 API 密钥
5. 复制密钥到 `.env` 文件

## 推荐配置方案

### 方案 1：经济实惠型（中国用户）

适合：个人开发者、学习使用

```bash
# LLM
DEEPSEEK_API_KEY=your_key_here

# 可选：语音识别（免费）
# 安装: pip install faster-whisper
# 无需配置，自动使用本地模型
```

**优点**：
- 成本低（DeepSeek 性价比高）
- 中文支持好
- 代码能力强

**缺点**：
- 功能相对基础
- 无网络搜索、图像生成等高级功能

### 方案 2：功能完整型

适合：专业开发者、团队使用

```bash
# LLM（选择其一）
OPENROUTER_API_KEY=your_key_here  # 推荐：访问 200+ 模型
# 或
ANTHROPIC_API_KEY=your_key_here   # Claude 官方 API

# 网络搜索
EXA_API_KEY=your_key_here

# 图像生成
FAL_KEY=your_key_here

# GitHub 集成
GITHUB_TOKEN=your_token_here

# 语音识别（可选）
GROQ_API_KEY=your_key_here  # 免费额度
```

**优点**：
- 功能完整
- 支持所有工具集
- 可以处理复杂任务

**缺点**：
- 需要配置多个服务
- 成本相对较高

### 方案 3：中国大陆优化型

适合：中国大陆用户，需要稳定访问

```bash
# LLM（选择其一或多个）
DEEPSEEK_API_KEY=your_key_here     # 推荐：性价比高
GLM_API_KEY=your_key_here          # 智谱 AI
KIMI_API_KEY=your_key_here         # Kimi/Moonshot
MINIMAX_CN_API_KEY=your_key_here   # MiniMax 中国区

# 其他功能按需配置
```

**优点**：
- 无需代理，访问稳定
- 中文支持优秀
- 符合国内合规要求

**缺点**：
- 部分高级功能可能受限

## 详细配置说明

### LLM 提供商配置

#### OpenRouter（推荐 - 一站式解决方案）

**优势**：
- 一个 API 访问 200+ 模型
- 按使用付费，无月费
- 支持所有主流模型（Claude, GPT-4, Gemini 等）

**配置步骤**：
1. 访问 https://openrouter.ai/keys
2. 注册并创建 API 密钥
3. 添加到 `.env`：
   ```bash
   OPENROUTER_API_KEY=sk-or-v1-xxxxx
   ```
4. 运行 `qiqiclaw model` 选择模型

**推荐模型**：
- `anthropic/claude-opus-4.7` - 最强大
- `anthropic/claude-sonnet-4.6` - 平衡性能和成本
- `deepseek/deepseek-v4-pro` - 高性价比

#### Anthropic Claude（官方 API）

**优势**：
- 官方 API，稳定可靠
- 最新模型优先支持
- 企业级支持

**配置步骤**：
1. 访问 https://console.anthropic.com/
2. 创建 API 密钥
3. 添加到 `.env`：
   ```bash
   ANTHROPIC_API_KEY=sk-ant-xxxxx
   ```

**定价**（参考）：
- Claude Opus 4.7: $15/1M input, $75/1M output
- Claude Sonnet 4.6: $3/1M input, $15/1M output
- Claude Haiku 4.5: $0.25/1M input, $1.25/1M output

#### DeepSeek（高性价比）

**优势**：
- 极高性价比
- 中文支持优秀
- 代码能力强

**配置步骤**：
1. 访问 https://platform.deepseek.com/
2. 注册并创建 API 密钥
3. 添加到 `.env`：
   ```bash
   DEEPSEEK_API_KEY=sk-xxxxx
   ```

**定价**（参考）：
- DeepSeek-V4-Pro: ¥0.5/1M tokens（约 $0.07）

### 工具服务配置

#### 网络搜索

**为什么需要**：启用 `/web` 工具集，让 AI 可以搜索网络信息

**推荐服务**：Exa AI

**配置步骤**：
1. 访问 https://exa.ai/
2. 注册并获取 API 密钥
3. 添加到 `.env`：
   ```bash
   EXA_API_KEY=your_key_here
   ```

**替代方案**：
- Tavily: https://tavily.com/
- Firecrawl: https://firecrawl.dev/

#### 图像生成

**为什么需要**：启用 `/image_gen` 工具集

**推荐服务**：Fal.ai

**配置步骤**：
1. 访问 https://fal.ai/
2. 注册并获取 API 密钥
3. 添加到 `.env`：
   ```bash
   FAL_KEY=your_key_here
   ```

**替代方案**：
- 使用 OpenAI DALL-E（需要 OPENAI_API_KEY）

#### GitHub 集成

**为什么需要**：访问 Skills Hub，使用 GitHub 相关功能

**配置步骤**：
1. 访问 https://github.com/settings/tokens
2. 创建 Personal Access Token
3. 选择权限：`repo`, `read:org`
4. 添加到 `.env`：
   ```bash
   GITHUB_TOKEN=ghp_xxxxx
   ```

#### 语音识别（可选）

**免费方案**：本地 faster-whisper

```bash
# 安装
pip install faster-whisper

# 无需配置，自动使用
```

**云端方案**：Groq（免费额度）

```bash
# 1. 访问 https://console.groq.com/
# 2. 获取 API 密钥
# 3. 添加到 .env
GROQ_API_KEY=gsk_xxxxx
```

### 消息网关配置

#### Telegram Bot

**配置步骤**：
1. 在 Telegram 中与 @BotFather 对话
2. 发送 `/newbot` 创建新 Bot
3. 按提示设置名称
4. 获取 Bot Token
5. 添加到 `.env`：
   ```bash
   TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
   ```
6. 运行网关：
   ```bash
   qiqiclaw gateway setup
   qiqiclaw gateway start
   ```

#### Discord Bot

**配置步骤**：
1. 访问 https://discord.com/developers/applications
2. 创建新应用
3. 在 Bot 页面创建 Bot
4. 复制 Token
5. 添加到 `.env`：
   ```bash
   DISCORD_BOT_TOKEN=xxxxx
   ```

## 配置验证

### 检查配置

```bash
# 运行诊断工具
qiqiclaw doctor

# 查看当前配置
qiqiclaw config

# 查看工具状态
qiqiclaw tools list
```

### 测试功能

```bash
# 测试 LLM
qiqiclaw chat -q "你好，请介绍一下自己"

# 测试网络搜索（需要配置搜索 API）
qiqiclaw chat -q "搜索最新的 Python 3.12 新特性"

# 测试语音（需要配置 TTS）
qiqiclaw chat -q "用语音说：你好世界"
```

## 常见问题

### Q1: 如何选择 LLM 提供商？

**A**: 根据需求选择：
- **新手/学习**：DeepSeek（性价比高，中文好）
- **专业开发**：OpenRouter 或 Anthropic（功能完整）
- **中国用户**：DeepSeek、GLM、Kimi（访问稳定）
- **预算充足**：Anthropic Claude Opus（最强性能）

### Q2: 必须配置所有 API 密钥吗？

**A**: 不需要。最少只需配置一个 LLM 提供商即可使用。其他功能（搜索、图像生成等）是可选的，按需配置。

### Q3: API 密钥安全吗？

**A**: 
- API 密钥存储在 `~/.qiqiclaw/.env` 文件中
- 该文件权限应设置为仅当前用户可读：`chmod 600 ~/.qiqiclaw/.env`
- 不要将 `.env` 文件提交到 Git 仓库
- 定期轮换 API 密钥

### Q4: 如何估算使用成本？

**A**: 
- 大多数提供商按 token 计费
- 1M tokens ≈ 750,000 英文单词 ≈ 500,000 中文字符
- 使用 `qiqiclaw insights` 查看使用统计
- 建议先设置 API 使用限额

### Q5: 配置后工具仍显示"缺失"？

**A**: 
1. 确认 API 密钥正确添加到 `.env`
2. 运行 `qiqiclaw doctor` 检查配置
3. 重启 QiQi Claw：`qiqiclaw` 或 `qiqiclaw gateway restart`
4. 检查 API 密钥是否有效（未过期、有余额）

### Q6: 中国大陆访问国外 API 的问题？

**A**: 
- **推荐方案**：使用国内提供商（DeepSeek、GLM、Kimi）
- **替代方案**：配置代理
  ```bash
  # 在 .env 中添加
  HTTP_PROXY=http://proxy.example.com:8080
  HTTPS_PROXY=http://proxy.example.com:8080
  ```

### Q7: 如何从 qiqiclaw 迁移配置？

**A**: 
```bash
# 方法 1: 直接使用旧配置（自动兼容）
# QiQi Claw 会自动检测 ~/.qiqiclaw/ 目录

# 方法 2: 手动迁移
mv ~/.qiqiclaw ~/.qiqiclaw

# 方法 3: 复制配置
cp ~/.qiqiclaw/.env ~/.qiqiclaw/.env
cp ~/.qiqiclaw/config.yaml ~/.qiqiclaw/config.yaml
```

### Q8: 如何更新 API 密钥？

**A**: 
```bash
# 方法 1: 直接编辑
nano ~/.qiqiclaw/.env

# 方法 2: 使用命令
qiqiclaw config set <key> <value>

# 方法 3: 重新运行设置向导
qiqiclaw setup
```

## 获取帮助

- **文档**：查看 README_CN.md
- **诊断**：运行 `qiqiclaw doctor`
- **配置**：运行 `qiqiclaw setup`
- **问题反馈**：GitHub Issues

## 相关文档

- [README_CN.md](README_CN.md) - 项目总览
- [.env.example.cn](.env.example.cn) - 配置文件模板
- [BRAND_FIX_REPORT.md](BRAND_FIX_REPORT.md) - 品牌替换报告
