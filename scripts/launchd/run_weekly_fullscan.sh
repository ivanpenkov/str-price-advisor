#!/usr/bin/env bash
# ==============================================================================
# Job 3: Weekly Full 12-Month Market Scan & Multi-Platform Audit (Sundays 2:00 AM)
# Scrapes all open intervals across 12 months, runs multi-channel comparisons
# across Airbnb, VRBO, Booking.com, and Kivoya, generates full static HTML,
# and pushes updates to GitHub Pages.
# ==============================================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOG_DIR="$HOME/Library/Logs/str-price-advisor"
LOG_FILE="$LOG_DIR/weekly_fullscan.log"

mkdir -p "$LOG_DIR"

echo "=================================================================" >> "$LOG_FILE"
echo "⏰ [$(date '+%Y-%m-%d %H:%M:%S')] Starting Weekly Full 12-Month Market & Multi-Platform Scan..." >> "$LOG_FILE"

cd "$PROJECT_ROOT" || exit 1

# Prevent system sleep during multi-hour scraping execution using caffeinate
caffeinate -i "$PROJECT_ROOT/.venv/bin/python" -m src.cli run --weekly --compare-platforms --push >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "✅ [$(date '+%Y-%m-%d %H:%M:%S')] Weekly Full Scan completed successfully." >> "$LOG_FILE"
    osascript -e 'display notification "Full 12-month market scan & channel comparison complete." with title "✅ Weekly Audit Complete" sound name "Glass"' 2>/dev/null || true
else
    echo "❌ [$(date '+%Y-%m-%d %H:%M:%S')] Weekly Full Scan failed with exit code $EXIT_CODE." >> "$LOG_FILE"
    osascript -e 'display notification "Weekly full scan failed. Check weekly_fullscan.log." with title "❌ Weekly Audit Failed" sound name "Basso"' 2>/dev/null || true
fi

exit $EXIT_CODE

