#!/usr/bin/env bash
# ==============================================================================
# STR Price Advisor — Mobile ntfy Bridge Service Wrapper
# Sets up environment and launches the two-way ntfy bridge daemon.
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
export PYTHONPATH="$PROJECT_ROOT:${PYTHONPATH:-}"
export PYTHONUNBUFFERED="1"
export LANG="en_US.UTF-8"

cd "$PROJECT_ROOT"

if [[ -f "$PROJECT_ROOT/.env" ]]; then
    set -a
    source "$PROJECT_ROOT/.env" 2>/dev/null || true
    set +a
fi

if [[ -f "$PROJECT_ROOT/.venv/bin/activate" ]]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting Antigravity Mobile ntfy Bridge daemon..."
exec python3 -u "$PROJECT_ROOT/scripts/mobile_ntfy_bridge.py"
