#!/bin/bash
# Start Feishu integration in SDK mode (long connection) with auto-restart
# This is the recommended mode - no webhook URL or tunnel needed!

set -e

# 请根据你的环境修改以下路径
CONDA_HOME="${CONDA_HOME:-$HOME/anaconda3}"
PROJECT_DIR="${PROJECT_DIR:-$(pwd)}"

# Activate conda environment
source "$CONDA_HOME/bin/activate" kimi_feishu

cd "$PROJECT_DIR"

# Configuration
RESTART_EXIT_CODE=42
MAX_RESTARTS=10
RESTART_DELAY=3
restart_count=0

# Function to cleanup processes on script exit
cleanup() {
    echo "🛑 Shutting down Feishu integration..."
    # Clean up any remaining processes on the gateway port
    PORT=18789
    PID=$(lsof -ti:$PORT 2>/dev/null || echo "")
    if [ -n "$PID" ]; then
        echo "Cleaning up process on port $PORT..."
        kill -9 $PID 2>/dev/null || true
    fi
    exit 0
}

# Trap signals
trap cleanup SIGINT SIGTERM

echo "🚀 Starting Feishu integration in SDK mode with auto-restart..."
echo ""
echo "Features:"
echo "  ✅ No webhook URL needed"
echo "  ✅ No tunnel/穿透 tools required"
echo "  ✅ Direct WebSocket connection to Feishu"
echo "  ✅ Auto-restart on code changes (via /restart command)"
echo ""
echo "Send /restart in Feishu to reload the bot after code changes"
echo ""

# Main restart loop
while [ $restart_count -lt $MAX_RESTARTS ]; do
    # Clean up any existing processes on the gateway port before starting
    PORT=18789
    PID=$(lsof -ti:$PORT 2>/dev/null || echo "")
    if [ -n "$PID" ]; then
        echo "Cleaning up existing process on port $PORT..."
        kill -9 $PID 2>/dev/null || true
        sleep 1
    fi
    
    restart_count=$((restart_count + 1))
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "🔄 Starting attempt $restart_count/$MAX_RESTARTS"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    
    # Run the SDK server
    python -m kimi_cli.feishu
    exit_code=$?
    
    # Check if restart was requested (exit code 42)
    if [ $exit_code -eq $RESTART_EXIT_CODE ]; then
        echo ""
        echo "🔄 Restart requested, reloading in ${RESTART_DELAY}s..."
        sleep $RESTART_DELAY
        continue
    fi
    
    # Normal exit or error - don't restart
    echo ""
    echo "👋 Feishu integration stopped (exit code: $exit_code)"
    break
done

if [ $restart_count -ge $MAX_RESTARTS ]; then
    echo ""
    echo "⚠️ Maximum restart attempts ($MAX_RESTARTS) reached. Giving up."
    exit 1
fi
