#!/usr/bin/env bash
# Start the alpaca_core local HTTP service on 127.0.0.1 (paper only). Usage:
#   scripts/run_server.sh [--port 8765] [--enable-orders]
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python -m alpaca_core serve "$@"
