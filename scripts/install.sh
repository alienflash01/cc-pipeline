#!/bin/bash
# cc-pipeline 安装脚本
# 用法：bash scripts/install.sh [--dev]
set -euo pipefail

echo "🌙 cc-pipeline 安装脚本"
echo ""

# ─── 颜色输出 ───
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✅ $1${NC}"; }
warn() { echo -e "${YELLOW}⚠️  $1${NC}"; }
fail() { echo -e "${RED}❌ $1${NC}"; exit 1; }

# ─── 检测 Python ───
echo "─── Python ───"
if command -v python3 &>/dev/null; then
    PYVER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    PYOK=$(python3 -c 'import sys; print(1 if sys.version_info >= (3, 10) else 0)')
    if [ "$PYOK" = "1" ]; then
        ok "Python $PYVER"
    else
        fail "Python $PYVER < 3.10，请升级"
    fi
else
    fail "Python3 未安装"
fi

# ─── 检测 Git ───
echo "─── Git ───"
if command -v git &>/dev/null; then
    GITVER=$(git --version)
    ok "$GITVER"
else
    fail "Git 未安装"
fi

# ─── 检测 Claude Code（可选）───
echo "─── Claude Code ───"
if command -v claude &>/dev/null; then
    ok "Claude Code CLI 已安装"
else
    warn "Claude Code CLI 未安装（仅 shell executor 模式不需要）"
    echo "   安装：npm i -g @anthropic-ai/claude-code"
fi

# ─── 检测 pip ───
echo "─── pip ───"
PIP_CMD=""
if command -v pip &>/dev/null; then
    PIP_CMD="pip"
elif command -v pip3 &>/dev/null; then
    PIP_CMD="pip3"
else
    fail "pip 未安装"
fi
ok "pip: $PIP_CMD"

# ─── 检测 gh CLI（可选）───
echo "─── GitHub CLI ───"
if command -v gh &>/dev/null; then
    ok "gh CLI 已安装（支持自动 PR）"
else
    warn "gh CLI 未安装（自动 PR 不可用）"
    echo "   安装：https://cli.github.com/"
fi

# ─── 安装 cc-pipeline ───
echo ""
echo "─── 安装 cc-pipeline ───"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

DEV_FLAG=""
if [[ "$*" == *"--dev"* ]]; then
    DEV_FLAG="[dev]"
    echo "   安装开发依赖（pytest, pytest-cov, pytest-mock）"
fi

# 检测 PEP 668（externally-managed）
if [ -f /usr/lib/python3/EXTERNALLY-MANAGED ] || python3 -c "import sysconfig; sys.exit(0 if 'externally-managed' in (sysconfig.get_path('stdlib') or '') else 1)" 2>/dev/null; then
    echo "   检测到 PEP 668，使用 --break-system-packages"
    $PIP_CMD install -e ".$DEV_FLAG" --break-system-packages
else
    $PIP_CMD install -e ".$DEV_FLAG"
fi

ok "cc-pipeline 安装完成"

# ─── 验证 ───
echo ""
echo "─── 验证 ───"
if command -v cc-pipeline &>/dev/null; then
    VERSION=$(cc-pipeline --version 2>&1 || echo "unknown")
    ok "cc-pipeline $VERSION"
else
    warn "cc-pipeline 命令不在 PATH 中"
    echo "   可能需要重新打开终端或运行：source ~/.bashrc"
    echo "   或直接使用：python3 -m cc_pipeline --version"
fi

# ─── 运行测试（--dev 模式）───
if [[ "$*" == *"--dev"* ]]; then
    echo ""
    echo "─── 运行测试 ───"
    if command -v pytest &>/dev/null; then
        pytest tests/ -q 2>&1 | tail -3
    fi
fi

echo ""
ok "安装完成！"
echo ""
echo "下一步："
echo "  1. 体验示例：cd examples/quickstart-shell && ./run.sh   （纯 shell，0 成本 0 依赖）"
echo "  2. 生成配置：cc-pipeline init && cc-pipeline check"
echo "  3. 运行：cc-pipeline run config.yaml"
echo "  4. 文档：docs/USER-GUIDE.md"
