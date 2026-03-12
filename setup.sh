#!/bin/bash
# Install mcp-sessions MCP server for Claude Code
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "Creating virtual environment..."
python3 -m venv venv
./venv/bin/pip install -q -r requirements.txt

echo ""
echo "Done! Add this to your ~/.claude.json under \"mcpServers\":"
echo ""
cat <<EOF
"sessions": {
  "type": "stdio",
  "command": "$SCRIPT_DIR/venv/bin/python",
  "args": ["$SCRIPT_DIR/server.py"]
}
EOF
echo ""
echo "Then restart Claude Code."
