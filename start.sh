#!/bin/bash
# 诡秘之主 活动报名系统 - 启动脚本

cd "$(dirname "$0")"

export PORT=${PORT:-3000}
export DB_PATH=${DB_PATH:-$(pwd)/data/signup.db}

echo "========================================"
echo "  诡秘之主 · 活动报名系统"
echo "========================================"
echo ""
echo "数据库: $DB_PATH"
echo "端口: $PORT"
echo ""

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "❌ 未找到 python3，请先安装 Python 3"
    exit 1
fi

# 检查依赖
python3 -c "import flask" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "未检测到 flask，正在安装依赖..."
    pip3 install --user flask flask-cors waitress
    if [ $? -ne 0 ]; then
        echo "❌ 依赖安装失败"
        exit 1
    fi
fi

echo ""
echo "🚀 启动服务器..."
echo "访问地址: http://localhost:$PORT"
echo "按 Ctrl+C 停止"
echo ""

python3 app.py
