#!/usr/bin/env bash
# Launch Alpaca's official MCP server in paper mode.
# Maps this project's env var names onto the ones alpaca-mcp-server expects.
# Credentials stay in the environment; this script never prints them.
export ALPACA_API_KEY="${ALPACA_API_KEY_ID}"
export ALPACA_SECRET_KEY="${ALPACA_API_SECRET_KEY}"
export ALPACA_PAPER_TRADE=true
exec uvx alpaca-mcp-server "$@"
