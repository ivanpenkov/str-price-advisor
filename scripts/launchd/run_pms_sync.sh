#!/usr/bin/env bash
# ==============================================================================
# Job 1: Daily Streamline OwnerX PMS Reservation Sync (6:00 AM)
# Ingests recent reservations, updates SQLite & JSON databases, and refreshes dashboard.
# ==============================================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOG_DIR="$HOME/Library/Logs/str-price-advisor"
LOG_FILE="$LOG_DIR/pms_sync.log"

mkdir -p "$LOG_DIR"

echo "=================================================================" >> "$LOG_FILE"
echo "⏰ [$(date '+%Y-%m-%d %H:%M:%S')] Starting Daily PMS Reservation Sync..." >> "$LOG_FILE"

cd "$PROJECT_ROOT" || exit 1

# Prevent system sleep during execution using caffeinate
caffeinate -i "$PROJECT_ROOT/.venv/bin/python" -m src.cli sync-reservations --days-back 60 --dashboard --push >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "✅ [$(date '+%Y-%m-%d %H:%M:%S')] Daily PMS Sync completed successfully." >> "$LOG_FILE"
    osascript -e 'display notification "Villa del Sol reservations synced & pushed to GitHub." with title "✅ PMS Sync Complete" sound name "Glass"' 2>/dev/null || true
else
    echo "❌ [$(date '+%Y-%m-%d %H:%M:%S')] Daily PMS Sync failed with exit code $EXIT_CODE." >> "$LOG_FILE"
    osascript -e 'display notification "Error syncing Villa del Sol reservations. Check pms_sync.log." with title "❌ PMS Sync Failed" sound name "Basso"' 2>/dev/null || true
fi

exit $EXIT_CODE

