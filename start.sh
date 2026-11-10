#!/bin/bash
# Pixelle-Video 一键启动

PROJECT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)" || exit 1
LOG_DIR="$PROJECT_DIR/.logs"
mkdir -p "$LOG_DIR"

# 检查并关闭已有进程
pkill -f "orchestrator.worker" 2>/dev/null
pkill -f "uvicorn api.app:app" 2>/dev/null
pkill -f "next dev" 2>/dev/null
sleep 1

cd "$PROJECT_DIR"
source .venv/bin/activate

# 启动 FastAPI
nohup uvicorn api.app:app --host 0.0.0.0 --port 8000 > "$LOG_DIR/api.log" 2>&1 &
echo "✓ FastAPI started (port 8000)"

# 启动 Worker
nohup python -m orchestrator.worker > "$LOG_DIR/worker.log" 2>&1 &
echo "✓ Worker started"

# 启动 Dashboard
cd "$PROJECT_DIR/dashboard"
nohup npm run dev > "$LOG_DIR/dashboard.log" 2>&1 &
echo "✓ Dashboard starting..."

# 等待 dashboard 启动
sleep 4

# 获取实际端口
PORT=$(grep -o "localhost:[0-9]*" "$LOG_DIR/dashboard.log" | head -1 | cut -d: -f2)
PORT=${PORT:-3000}

echo ""
echo "🎬 Pixelle-Video 已启动"
echo "   打开浏览器: http://localhost:$PORT"
echo ""
echo "   日志目录: $LOG_DIR"
echo "   停止服务: $PROJECT_DIR/stop.sh"

# 自动打开浏览器
open "http://localhost:$PORT"
