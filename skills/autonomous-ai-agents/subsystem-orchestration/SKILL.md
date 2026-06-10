---
name: subsystem-orchestration
description: 每次执行任务时的子系统调度指南 — 决定用 Claude Code、Codex 还是 QiQi claw 直接处理，以及如何调用 ECC 专业 agent
version: 2.0.0
author: QiQi claw
tags: [orchestration, claude-code, codex, ecc, delegation, workflow]
---

# 子系统调度指南

每次接到用户任务时，按此流程决定由哪个子系统执行、如何执行。

---

## 一、快速决策树

```
用户任务
├── 纯知识问答 / 简单文件读写 / 配置修改
│   → QiQi claw 直接回答（不委派）
│
├── 代码编写 / 重构 / Bug 修复 / PR Review
│   → Claude Code（--dangerously-skip-permissions --max-turns 20-40）
│   → 优先用 ECC 专业 agent：@planner → @tdd-guide → @code-reviewer
│
├── 复杂调试（3+ 次尝试仍失败）
│   → Claude Code（--max-turns 40）
│   → 用 @build-error-resolver 或 @python-reviewer
│
├── 多模态任务（看图/视频理解、视觉 UI 测试）
│   → Codex（codex exec --full-auto -i 图片路径）
│
├── 日常通用任务 / 简单脚本 / 文件批量处理
│   → Codex（codex exec --full-auto）
│
├── 安全审查
│   → Claude Code + @security-reviewer agent
│   → 或用 /security-scan 命令
│
├── 多文件大规模重构 / 架构变更
│   → Claude Code：先 @planner 规划，再执行
│
├── 文档编写 / README / 项目文档
│   → Claude Code + @doc-updater agent
│
├── 并行多任务（前端+后端+测试同时）
│   → delegate_task 批量委派给多个 Claude Code 实例
│
└── 需要用户交互确认的任务
    → QiQi claw 自己处理，不要委派（子系统不能 clarify）
```

---

## 二、Claude Code 调用规范

### 基本调用模式

```bash
# 单次任务（最常用）
terminal(
    background=true,
    notify_on_complete=true,
    command="ANTHROPIC_BASE_URL='https://oneapi.hk' ANTHROPIC_AUTH_TOKEN='sk-gLEM9xE5gSXz22Dtk8FTqDBMng28tyyxgD6vfDJY9yjZzJvY' /home/szd111/bin/claude -p '任务描述' --dangerously-skip-permissions --max-turns 30",
    workdir="/path/to/project",
    timeout=300
)
# 然后用 process(action="wait") 等待结果
```

### ECC Agent 调用方式

在 Claude Code 的 prompt 中直接引用 agent：

```
"请 @planner 先做实现计划，然后根据计划 @tdd-guide 编写代码，最后 @code-reviewer 审查"
```

常用 Agent 组合：

| 任务类型 | Agent 链 |
|---------|---------|
| 新功能 | @planner → @tdd-guide → @code-reviewer |
| Bug 修复 | @code-explorer → @tdd-guide → @code-reviewer |
| 重构 | @architect → @refactor-cleaner → @code-reviewer |
| 安全审计 | @security-reviewer → @silent-failure-hunter |
| 构建失败 | @build-error-resolver（语言特定的用对应 agent） |
| 性能优化 | @performance-optimizer → @code-reviewer |
| PR Review | @code-reviewer + @pr-test-analyzer |
| 自主循环 | @loop-operator（监控多轮任务） |

### ECC Slash 命令

这些命令在交互模式下可用，在 `-p` 模式用自然语言描述等效操作：

| 命令 | 等效自然语言 |
|------|------------|
| `/code-review` | "Review the current changes for quality and security" |
| `/plan` | "Create an implementation plan for..." |
| `/feature-dev` | "Develop this feature with TDD" |
| `/harness-audit` | "Audit harness configuration quality" |
| `/quality-gate` | "Run quality checks before commit" |
| `/refactor-clean` | "Clean dead code and refactor" |
| `/test-coverage` | "Analyze test coverage" |

### 关键参数

```
--dangerously-skip-permissions  # 自动批准所有操作
--max-turns 20                  # 简单任务
--max-turns 30                  # 中等任务
--max-turns 40                  # 复杂任务/调试
--allowedTools "Read,Edit,Write,Bash"  # 限制可用工具
--model sonnet                  # 指定模型
--effort high                   # 推理深度：low/medium/high/max
```

---

## 三、Codex 调用规范

### 基本调用模式

```bash
# 单次任务
terminal(
    pty=true,
    background=true,
    command="cd /path/to/git/repo && echo '任务描述' | codex exec --full-auto",
    timeout=300
)

# 多模态任务（附加图片）
terminal(
    pty=true,
    command="cd /path && codex exec --full-auto -i /path/to/image.png -s danger-full-access --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check --cd /path",
    timeout=300
)
```

### Codex 多代理模式（新）

ECC 已配置 Codex 的 multi_agent + 3 个专用角色：

```
# 在 Codex 内部调用
/agent explorer    → 只读代码探索
/agent reviewer    → 正确性和安全审查
/agent docs_researcher → API 文档查证
```

### Codex vs Claude Code 选择

| 场景 | 用 Codex | 用 Claude Code |
|------|---------|---------------|
| 视觉/多模态任务 | 支持 | 需额外设置 |
| 复杂推理 | 一般 | 强 |
| 安全性（hooks 拦截） | 不支持 | 支持 |
| 速度 | 快 | 中等 |
| MCP 工具 | 支持 | 支持 |
| 多代理 | /agent 模式 | @agent 模式 |

---

## 四、并行委派模式

当多个独立任务可并行时，用 delegate_task：

```
delegate_task(
    tasks=[
        {goal: "修后端 Bug", context: "...", toolsets: ["terminal", "file"]},
        {goal: "写前端测试", context: "...", toolsets: ["terminal", "file"]},
        {goal: "更新文档", context: "...", toolsets: ["terminal", "file"]},
    ]
)
```

---

## 五、执行前检查清单

每次接到任务时，自问：

1. 这是纯信息/知识问题吗？ → 自己直接回答
2. 需要写/改代码吗？ → Claude Code
3. 涉及看图片/视频吗？ → Codex
4. 需要安全审查吗？ → Claude Code + @security-reviewer
5. 需要用户交互确认吗？ → 自己处理，不委派
6. 任务超过 5 步吗？ → 先 plan，再执行
7. 可以并行吗？ → delegate_task 批量
8. 是否需要在 prompt 中明确指定 ECC agent？

---

## 六、常用任务模板

### 模板 1：新功能开发

```
1. QiQi claw 理解需求
2. Claude Code: "@planner 制定实现计划" --max-turns 15
3. Claude Code: "@tdd-guide 按计划实现" --max-turns 30
4. Claude Code: "@code-reviewer 审查改动" --max-turns 10
5. QiQi claw 汇总结果反馈用户
```

### 模板 2：Bug 修复

```
1. QiQi claw 收集错误信息
2. Claude Code: "@code-explorer 定位问题根因，然后用 @tdd-guide 修复" --max-turns 30
3. Claude Code: "@code-reviewer 验证修复" --max-turns 10
```

### 模板 3：代码审查

```
Claude Code: "@code-reviewer 审查 git diff。@security-reviewer 检查安全漏洞" --max-turns 15
```

### 模板 4：大规模重构

```
1. Claude Code: "@architect 设计重构方案" --max-turns 20
2. Claude Code: "@refactor-cleaner 按方案执行重构" --max-turns 40
3. Claude Code: "@code-reviewer + @e2e-runner 验证完整性" --max-turns 20
```

---

## 七、WSL2 特别注意事项

- Claude Code 调用必须带 `ANTHROPIC_BASE_URL` 和 `ANTHROPIC_AUTH_TOKEN` 环境变量
- Codex 调用必须带 `pty=true`
- Windows 路径用 `/mnt/c/...` 格式
- 大文件/大量小文件操作避免 shell rm -rf，用 Python shutil.rmtree
- pip/npm 下载大包可能超时，换国内镜像或 Windows 侧执行
