#!/usr/bin/env bash
# Start the Household Memory Bridge web simulator.
# The FitLog MCP server must already be running (default http://127.0.0.1:8765).
set -euo pipefail
cd "$(dirname "$0")"

PORT="${SIMULATOR_PORT:-8080}"
echo "Household Memory Bridge simulator -> http://127.0.0.1:${PORT}/"
echo "MCP upstream                      -> ${FITLOG_MCP_URL:-http://127.0.0.1:8765}/mcp"
exec python3 server.py
