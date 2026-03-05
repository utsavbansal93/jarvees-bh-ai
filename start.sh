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
echo "  Open in browser: http://localhost:8000"
echo "  Stop server:     Ctrl+C"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

python3 -m uvicorn main:app --reload --port 8000
