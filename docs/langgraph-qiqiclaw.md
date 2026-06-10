# LangGraph 与 QiQiClaw 融合

本机融合采用低风险方案：LangGraph 负责编排状态流，QiQiClaw 负责智能执行、记忆、技能、MCP 工具和模型路由。

## 命令

```bash
qiqiclaw langgraph --dry-run "把这个任务拆成三步"
qiqiclaw langgraph --json --dry-run "检查项目状态"
qiqiclaw langgraph "执行一个真实任务"
```

## 架构

- `qiqiclaw_cli.langgraph_runner` 构建 `StateGraph`。
- 图中 `qiqiclaw` 节点调用现有 `qiqiclaw_cli.oneshot._run_agent()`。
- `--dry-run` 使用确定性本地 runner，不调用模型，适合验证安装和自动化测试。
- 不替换 QiQiClaw/Hermes 兼容后端循环，不移除 `hermes_cli`、`HERMES_*` 和桌面 IPC 兼容层。

## 后续扩展点

- 增加多节点图：规划、执行、复核、汇总。
- 增加条件边：按状态决定重试、人工介入或结束。
- 增加 checkpointer：为长任务提供断点续跑。
