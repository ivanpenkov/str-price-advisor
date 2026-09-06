#!/usr/bin/env bash
# ==============================================================================
# Job 2: Daily 90-Day Quick Pricing & Market Scan (6:15 AM)
# Scrapes live competitor rates across the first 12 upcoming intervals,
# applies 6-factor quality adjustments, generates reports, and pushes to GitHub.
# ==============================================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOG_DIR="$HOME/Library/Logs/str-price-advisor"
LOG_FILE="$LOG_DIR/daily_quickscan.log"

mkdir -p "$LOG_DIR"

echo "=================================================================" >> "$LOG_FILE"
echo "⏰ [$(date '+%Y-%m-%d %H:%M:%S')] Starting Daily 90-Day Quick Market Scan..." >> "$LOG_FILE"

cd "$PROJECT_ROOT" || exit 1

# Prevent system sleep during scraping using caffeinate
caffeinate -i "$PROJECT_ROOT/.venv/bin/python" -m src.cli run --quick --limit 12 --push >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "✅ [$(date '+%Y-%m-%d %H:%M:%S')] Daily Quick Scan completed successfully." >> "$LOG_FILE"
    osascript -e 'display notification "90-day quick market scan finished & dashboard updated." with title "✅ Quick Scan Complete" sound name "Glass"' 2>/dev/null || true
else
    echo "❌ [$(date '+%Y-%m-%d %H:%M:%S')] Daily Quick Scan failed with exit code $EXIT_CODE." >> "$LOG_FILE"
    osascript -e 'display notification "Market quick scan failed. Check daily_quickscan.log." with title "❌ Quick Scan Failed" sound name "Basso"' 2>/dev/null || true
fi

exit $EXIT_CODE

