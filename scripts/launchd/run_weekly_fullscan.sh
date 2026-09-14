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

# Ensure stale child processes or forwarders from previous sessions are cleanly reaped
cleanup() {
    pkill -f "pproxy" 2>/dev/null || true
    pkill -f "chrome-headless-shell" 2>/dev/null || true
}
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM
trap cleanup EXIT
cleanup

# Note: Pre-flight stealth proxy verification is performed in-process by start_pool()
# right before scraping, preventing NordVPN AAA linger quota self-poisoning (RCA-6).

# Prevent system sleep during multi-hour scraping execution using caffeinate
START_TS=$(date +%s)
caffeinate -i "$PROJECT_ROOT/.venv/bin/python" -u -m src.cli run --weekly --force --compare-platforms --push >> "$LOG_FILE" 2>&1
EXIT_CODE=$?
END_TS=$(date +%s)
DURATION_SEC=$((END_TS - START_TS))
HOURS=$((DURATION_SEC / 3600))
MINUTES=$(((DURATION_SEC % 3600) / 60))
SECONDS=$((DURATION_SEC % 60))
if [ $HOURS -gt 0 ]; then
    ELAPSED_FMT="${HOURS}h ${MINUTES}m ${SECONDS}s (${DURATION_SEC}s total)"
else
    ELAPSED_FMT="${MINUTES}m ${SECONDS}s (${DURATION_SEC}s total)"
fi

if [ $EXIT_CODE -eq 0 ]; then
    echo "✅ [$(date '+%Y-%m-%d %H:%M:%S')] Weekly Full Scan completed successfully in $ELAPSED_FMT." >> "$LOG_FILE"
    osascript -e "display notification \"Full 12-month market scan & channel comparison complete in $ELAPSED_FMT.\" with title \"✅ Weekly Audit Complete\" sound name \"Glass\"" 2>/dev/null || true
else
    echo "❌ [$(date '+%Y-%m-%d %H:%M:%S')] Weekly Full Scan failed after $ELAPSED_FMT with exit code $EXIT_CODE." >> "$LOG_FILE"
    osascript -e "display notification \"Weekly full scan failed after $ELAPSED_FMT. Check weekly_fullscan.log.\" with title \"❌ Weekly Audit Failed\" sound name \"Basso\"" 2>/dev/null || true
fi

exit $EXIT_CODE

