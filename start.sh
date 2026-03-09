#!/bin/bash
# Jarvees — start the web server + Node.js AI cascade service
# Usage: bash start.sh

cd "$(dirname "$0")"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Jarvees — starting server"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Install Python dependencies if needed
pip3 install fastapi "uvicorn[standard]" anthropic "google-genai" python-dotenv -q

# ── Node.js AI cascade service (claude-code-bridge + ai-model-cascade) ────────
NODE_PID=""
if command -v node &>/dev/null; then
    echo "  Starting Node.js AI cascade service..."
    if [ ! -d "ai_service/node_modules" ]; then
        echo "  Installing Node.js dependencies (first run, may take ~30s)..."
        npm install --prefix ai_service -q
    fi
    node --env-file-if-exists=.env ai_service/service.js &
    NODE_PID=$!
    echo "  AI cascade service started (claude-code-bridge + ai-model-cascade)"
else
    echo "  Node.js not found — using Python-only cascade"
fi

# Kill node service when this script exits (Ctrl+C or normal exit)
cleanup() {
    [ -n "$NODE_PID" ] && kill "$NODE_PID" 2>/dev/null
}
trap cleanup EXIT

echo ""
LOCAL_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "unknown")

echo "  Open on this Mac:  http://localhost:8000"
echo "  Open on iPhone:    http://$LOCAL_IP:8000"
echo "  Stop server:       Ctrl+C"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

python3 -m uvicorn main:app --reload --port 8000 --host 0.0.0.0
