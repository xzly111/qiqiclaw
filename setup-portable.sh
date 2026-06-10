#!/usr/bin/env bash
# ============================================================
# QiQi claw Portable Setup Script
# 在新电脑上运行此脚本完成一键部署
# 用法: bash setup-portable.sh
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORTABLE_CONFIG="$SCRIPT_DIR/portable_config"
QIQICLAW_HOME_DIR="$HOME/.qiqiclaw"

# --------------- 颜色输出 ---------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; NC='\033[0m'
info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERR]${NC}   $*"; exit 1; }

echo ""
echo "============================================================"
echo "  QiQi claw Portable Setup"
echo "  项目路径: $SCRIPT_DIR"
echo "============================================================"
echo ""

# --------------- 1. 检查系统依赖 ---------------
info "1/5  检查系统依赖..."

# Python
PYTHON=""
for py in python3.11 python3.12 python3; do
    if command -v "$py" &>/dev/null; then
        PY_VER=$("$py" --version 2>&1 | grep -oP '\d+\.\d+')
        MAJOR=$(echo "$PY_VER" | cut -d. -f1)
        MINOR=$(echo "$PY_VER" | cut -d. -f2)
        if [ "$MAJOR" -ge 3 ] && [ "$MINOR" -ge 11 ]; then
            PYTHON="$py"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    err "需要 Python >= 3.11。请先安装: sudo apt install python3.11 python3.11-venv"
fi
ok "Python: $($PYTHON --version)"

# pip
if ! $PYTHON -m pip --version &>/dev/null; then
    warn "pip 未安装，正在安装..."
    $PYTHON -m ensurepip --upgrade 2>/dev/null || \
        err "无法安装 pip"
fi
ok "pip: $($PYTHON -m pip --version 2>&1 | head -1)"

# git (可选)
if command -v git &>/dev/null; then
    ok "git: 已安装"
else
    warn "git 未安装 (可选，不影响基本使用)"
fi

# --------------- 2. 创建虚拟环境 ---------------
info "2/5  创建 Python 虚拟环境..."

VENV_DIR="$SCRIPT_DIR/.venv"
if [ ! -d "$VENV_DIR" ]; then
    $PYTHON -m venv "$VENV_DIR"
    ok "虚拟环境已创建: $VENV_DIR"
else
    ok "虚拟环境已存在: $VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

# --------------- 3. 安装依赖 ---------------
info "3/5  安装项目依赖..."

# 升级 pip
pip install --upgrade pip -q 2>&1 | tail -1

# 安装核心 + 推荐扩展
pip install -e '.[voice,cli,pty,mcp]' -q 2>&1 | tail -3
ok "依赖安装完成"

# --------------- 4. 配置 QiQi claw ---------------
info "4/5  配置 QiQi claw 运行环境..."

# 创建 ~/.qiqiclaw 目录
mkdir -p "$QIQICLAW_HOME_DIR"/{memories,sessions,logs,cron,skills,sandboxes,audio_cache}

# 复制配置文件
cp "$PORTABLE_CONFIG/config.yaml" "$QIQICLAW_HOME_DIR/config.yaml"
ok "config.yaml 已部署"

cp "$PORTABLE_CONFIG/SOUL.md" "$QIQICLAW_HOME_DIR/SOUL.md"
ok "SOUL.md 已部署"

cp "$PORTABLE_CONFIG/memories/MEMORY.md" "$QIQICLAW_HOME_DIR/memories/MEMORY.md" 2>/dev/null && \
    ok "MEMORY.md 已部署" || warn "MEMORY.md 不存在，跳过"

# 复制技能
if [ -d "$PORTABLE_CONFIG/skills" ]; then
    rsync -a --delete "$PORTABLE_CONFIG/skills/" "$QIQICLAW_HOME_DIR/skills/" 2>/dev/null || \
        cp -r "$PORTABLE_CONFIG/skills/"* "$QIQICLAW_HOME_DIR/skills/" 2>/dev/null || \
        warn "技能复制失败，首次运行时会自动拉取"
    ok "技能已部署"
fi

# 处理 .env
if [ ! -f "$QIQICLAW_HOME_DIR/.env" ]; then
    if [ -f "$PORTABLE_CONFIG/.env" ] && grep -q "DEEPSEEK_API_KEY" "$PORTABLE_CONFIG/.env" 2>/dev/null; then
        # 检查是否仍为占位符
        if grep -q "你的DeepSeek_API密钥" "$PORTABLE_CONFIG/.env" 2>/dev/null; then
            warn ".env 包含占位符，请编辑 $QIQICLAW_HOME_DIR/.env 填入 API 密钥"
            cp "$PORTABLE_CONFIG/.env" "$QIQICLAW_HOME_DIR/.env"
        else
            cp "$PORTABLE_CONFIG/.env" "$QIQICLAW_HOME_DIR/.env"
            ok ".env 已部署"
        fi
    else
        cp "$PORTABLE_CONFIG/.env.example" "$QIQICLAW_HOME_DIR/.env"
        warn ".env 模板已创建，请编辑 $QIQICLAW_HOME_DIR/.env 填入 API 密钥"
    fi
else
    ok ".env 已存在，保留现有配置"
fi

# --------------- 5. 验证 ---------------
info "5/5  验证安装..."

# 检查 qiqi 命令
if command -v qiqi &>/dev/null || command -v qiqiclaw &>/dev/null; then
    ok "QiQi claw 命令可用"
else
    warn "命令未在 PATH 中，使用 source .venv/bin/activate 后运行 qiqi"
fi

echo ""
echo "============================================================"
echo -e "  ${GREEN}安装完成!${NC}"
echo "============================================================"
echo ""
echo "  激活环境:"
echo "    cd $SCRIPT_DIR"
echo "    source .venv/bin/activate"
echo ""
echo "  启动:"
echo "    qiqi"
echo ""
echo "  API 密钥 (如未配置):"
echo "    编辑 $QIQICLAW_HOME_DIR/.env"
echo "    填入 DEEPSEEK_API_KEY=你的密钥"
echo ""
echo "  快速自检:"
echo "    qiqi doctor"
echo ""
echo "============================================================"
