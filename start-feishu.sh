#!/bin/bash
# Start Feishu integration in SDK mode (long connection)
# This is the recommended mode - no webhook URL or tunnel needed!

set -e

# 请根据你的环境修改以下路径
CONDA_HOME="${CONDA_HOME:-$HOME/anaconda3}"
PROJECT_DIR="${PROJECT_DIR:-$(pwd)}"

# Activate conda environment
source "$CONDA_HOME/bin/activate" kimi_feishu

cd "$PROJECT_DIR"

echo "🚀 Starting Feishu integration in SDK mode..."
echo ""
echo "Features:"
echo "  ✅ No webhook URL needed"
echo "  ✅ No tunnel/穿透 tools required"
echo "  ✅ Direct WebSocket connection to Feishu"
echo ""

# Clean up any existing processes on the gateway port
PORT=18789
PID=$(lsof -ti:$PORT 2>/dev/null || echo "")
if [ -n "$PID" ]; then
    echo "Cleaning up existing process on port $PORT..."
    kill -9 $PID 2>/dev/null || true
fi

# Run the SDK server
python -m kimi_cli.feishu
