#!/bin/bash
# Jarvees — start the web server
# Usage: bash start.sh

cd "$(dirname "$0")"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Jarvees — starting server"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Install dependencies if needed
pip3 install fastapi "uvicorn[standard]" anthropic python-dotenv -q

echo ""
LOCAL_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "unknown")

echo "  Open on this Mac:  http://localhost:8000"
echo "  Open on iPhone:    http://$LOCAL_IP:8000"
echo "  Stop server:       Ctrl+C"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

python3 -m uvicorn main:app --reload --port 8000 --host 0.0.0.0
