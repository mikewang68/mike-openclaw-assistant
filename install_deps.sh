#!/bin/bash
# install_deps.sh — 统一Python依赖安装脚本
# 用法: bash /home/node/.openclaw/workspace/install_deps.sh

PIP="/home/node/.local/bin/pip3"
if ! command -v pip3 &> /dev/null; then
    PIP="python3 -m pip"
fi

REQUIREMENTS="/home/node/.openclaw/workspace/requirements.txt"

echo "[deps] 检查 Python 依赖..."

# 读取requirements，逐个检查是否已安装
MISSING=""
while IFS= read -r line; do
    # 跳过空行和注释
    [[ -z "$line" || "$line" =~ ^# ]] && continue
    # 提取包名（去掉版本号和>,<,=符号）
    pkg=$(echo "$line" | sed 's/[><=!].*//' | xargs)
    [[ -z "$pkg" ]] && continue
    
    if python3 -c "import ${pkg//-/_}" 2>/dev/null; then
        echo "  ✓ ${pkg}"
    else
        echo "  ✗ ${pkg} — 将安装"
        MISSING="$MISSING $pkg"
    fi
done < "$REQUIREMENTS"

if [[ -n "$MISSING" ]]; then
    echo ""
    echo "[deps] 安装缺失的包:${MISSING}"
    $PIP install --break-system-packages${MISSING}
else
    echo "[deps] 所有依赖已满足，无需安装"
fi
