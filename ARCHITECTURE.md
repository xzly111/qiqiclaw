# QiQiClaw 架构说明与双内核收敛路线图

> 本文基于对源码的实测（2026-06-11），用于厘清 `hermes_cli` 与
> `qiqiclaw_cli` 的真实关系，纠正"双内核是重复拷贝、应退役 hermes_cli"
> 这一误判，并给出**安全、可增量验证**的收敛路线。

---

## 一、真实分层（与直觉相反）

常见误解：`hermes_cli` 是遗留内核、`qiqiclaw_cli` 是新内核，应退役前者。
**实测结论恰好相反：**

```
┌─────────────────────────────────────────────────────────┐
│  qiqiclaw_cli/   —— 较新的「展示层 / 入口层」              │
│  · main.py (CLI 入口，gateway 进程实际运行的就是它)        │
│  · 自带 config.py / auth.py / web_server.py（已分叉的副本）│
│  · 直接 import hermes_cli 的次数：0                        │
└───────────────────────────┬─────────────────────────────┘
                            │ 整个后端依赖向下
                            ▼
┌─────────────────────────────────────────────────────────┐
│  hermes_cli/     —— 真正的「后端地基」                     │
│  · config / plugins / middleware / auth / models          │
│  · env_loader / timeouts / dashboard_register / ...       │
│  · 被全项目 662 个文件 import（run_agent.py、cli.py、      │
│    model_tools.py、几乎所有 tools/*.py）                   │
└─────────────────────────────────────────────────────────┘
```

### 关键事实（实测）

| 指标 | 数值 | 含义 |
|------|------|------|
| 全项目 import `hermes_cli` 的文件数 | **662** | hermes_cli 是地基，不是遗留 |
| `qiqiclaw_cli` import `hermes_cli` | **0** | 展示层未复用地基，而是自带副本 |
| 重名文件数 | 71 | 表面"重复" |
| `hermes_cli/web_server.py` | 10,145 行 | 与 qiqiclaw 副本(4,453行)**已分叉** |
| `auth.py` 品牌归一后差异 | 4,140 行 | 不是拷贝，是两套独立演化的实现 |
| `config.py` 品牌归一后差异 | 2,474 行 | 同上 |

**结论**：这不是"两个内核竞争"，而是"一个较新的展示层(qiqiclaw_cli) +
一个被全项目依赖的后端地基(hermes_cli)"。两者中的同名文件
（config/auth/web_server）已经各自演化、分叉数千行，**不能简单合并**。

---

## 二、为什么"退役 hermes_cli"是错误方向

- 需要重写 **662 个文件**的 import（覆盖 run_agent、model_tools、
  全部 tools/），等于把房子的地基抽掉重浇。
- 项目无上游 git 仓库（本地 git 安全网为本次工作新建），无法依赖
  社区 review 兜底。
- **用户可见收益为零**：品牌名（窗口标题、CLI、横幅）早已是 QiQiClaw，
  剩余 `hermes` 均为内部符号。
- 失败模式严重：任一 import 漏改即在核心路径上 ImportError。

风险/收益严重失衡，故不执行强制合并。

---

## 三、安全收敛路线图（增量、可验证、可回滚）

目标不是"消灭 hermes_cli"，而是**消除展示层与地基之间真正重复的逻辑**，
让 `qiqiclaw_cli` 尽量复用 `hermes_cli`，减少分叉维护面。按风险从低到高：

### 阶段 1 — 冻结分叉（低风险）
- 在 `qiqiclaw_cli/config.py`、`auth.py`、`web_server.py` 顶部加注释，
  标明"此文件与 hermes_cli 对应文件已分叉，新增逻辑应优先下沉到
  hermes_cli 共享层"。
- 建立 CI 检查：禁止 qiqiclaw_cli 与 hermes_cli 之间新增重名文件。

### 阶段 2 — 抽取纯函数共享层（中风险，逐文件验证）
- 找出两边**逻辑确实一致**的纯函数（无品牌耦合的工具函数、解析器、
  校验器），下沉到 `hermes_cli`（或新建 `shared/`），两边改为 import。
- 每抽取一个，跑该模块相关测试，单独提交，可独立回滚。
- 优先级：`timeouts.py`、`env_loader.py`、`colors.py` 等小而稳定的模块。

### 阶段 3 — 命令注册表归一（中风险）
- `commands.py` 两边差异 590 行，多为描述文本（中/英）。
- 方案：抽取命令**定义**（名称、参数、handler 指针）到共享数据结构，
  描述文本走 i18n（见 `locales/`），消除结构性重复。

### 阶段 4 — web_server 收敛（高风险，最后做）
- 两边差异最大（7,654 行）。需先对齐 API 契约（见下节），再逐路由迁移。
- 必须在桌面端/dashboard 接口对齐完成后进行。

> 每个阶段都应：独立分支/提交 → 跑相关测试 → 验证 gateway 与 desktop
> 仍能启动 → 再进入下一阶段。任何阶段可随时停在已验证的提交上。

---

## 四、前后端接口契约（供桌面端对齐参考）

桌面端（Electron, `apps/desktop/`）通过以下契约连接后端：

| 契约项 | 值 | 定义位置 |
|--------|-----|---------|
| 后端根目录 | `HERMES_DESKTOP_HERMES_ROOT` | launcher → main.cjs:2024 |
| Python 解释器 | `HERMES_DESKTOP_PYTHON` | launcher → main.cjs:1014 |
| 数据目录 | `HERMES_HOME`（覆盖 get_hermes_home 默认值） | launcher |
| Gateway 端口下限 | `9120` (PORT_FLOOR) | main.cjs:94 |
| Gateway HTTP | `http://127.0.0.1:<port>` | main.cjs:4411 |
| Gateway WS | `ws://127.0.0.1:<port>/api/ws?token=<t>` | main.cjs:4422 |
| LangGraph API | `/api/langgraph/status`, `/api/langgraph/run` | src/app/langgraph/index.tsx:95,135 |

> ⚠️ 桌面端是**已编译的 194M 二进制**，硬编码读取 18 个 `HERMES_DESKTOP_*`
> 环境变量。任何对这些变量名的重命名都**必须同步重建该二进制**，否则桌面端
> 无法启动。详见品牌替换工作（Stage E）。

---

## 五、已知技术债（实测，供后续排期）

| 项 | 现状 | 位置 |
|----|------|------|
| 异常吞噬 | 全项目 3,630 处 `except Exception`，1,575 处直接 `pass`；核心路径已治理 | 见 git log Stage A |
| 巨型单文件 | cli.py 16,191 行 / run_agent.py 5,307 行 / hermes_state.py 4,366 行 | 根目录 |
| 无 mypy | 705 处 `# type: ignore` 但无 mypy 配置 | pyproject.toml |
| Ruff 仅 1 规则 | 仅启用 PLW1514 | pyproject.toml |
| 前端无 lockfile | apps/desktop 用 `^/~` 范围版本，无 package-lock.json | apps/desktop/package.json |

---

## 六、品牌替换（Stage E）结论与边界

目标曾设为"hermes → qiqiclaw 内部符号全替换"。实测后**收窄为安全可行的部分**：

### 已完成（安全、已验证）
- **桌面端 env 契约品牌化**：`HERMES_DESKTOP_*` → `QIQICLAW_DESKTOP_*`，
  采用向后兼容方案（main.cjs 读 `QIQICLAW || HERMES` 双名，launcher 导出双名）。
  前端构建（tsc+vite）验证通过。详见对应 commit。

### 实测后判定**不应做**的部分
| 类别 | 出现量 | 为何不改 |
|------|--------|---------|
| 模块名 `hermes_cli` | 被 665 文件 import | 改名=重写全后端 import，等同被否决的 Stage D 强制合并 |
| 模块名 `hermes_constants/state/logging` | 328/175/9 | 同上 |
| `HERMES_HOME` / `get_hermes_home()` | 450 文件 | 解析你的 **live state.db** 路径，误改丢数据（不可逆） |
| 功能字符串 | 8,380 处 | `~/.hermes/` 路径、`hermes_pkce` 认证标签、`HERMES_*` env、`hermes` CLI 命令、`HermesAgent` User-Agent —— 均与行为/外部状态耦合，改了破坏兼容性 |

### 关键事实：用户可见品牌已是 QiQiClaw
- CLI 横幅/版本、桌面窗口标题、命令名均已是 QiQiClaw（实测无残留 "Hermes" 文案）。
- 残留的 31k+ `hermes` 引用全是**内部符号/路径/类型名**，与功能逻辑逐行交织，
  无安全的批量改名方案，且**用户可见收益为零**。

**结论**：内部符号保持现状是正确工程决策。品牌已在用户层完成；继续深挖内部
符号是高风险、零收益的负功。后续若要彻底归一，应走 ARCHITECTURE 第三节的
增量收敛路线（建共享层 + 逐模块迁移 + 每步验证），而非一次性 rename。

---

*维护提示：本文档为 QiQiClaw 重构工作的架构基线。修改双内核结构前请先更新此文。*
