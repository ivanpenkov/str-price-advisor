#!/usr/bin/env bash
# ==============================================================================
# Job 2: Daily 90-Day Quick Pricing & Market Scan (6:15 AM)
# Scrapes live competitor rates across upcoming intervals, applies 6-factor quality adjustments,
# selectively performs multi-platform sweeps for intervals affected by Streamline price updates,
# generates reports, and pushes to GitHub.
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

# Ingest fresh guest reviews and platform scores (non-blocking)
echo "⭐ Syncing guest reviews and ratings..." >> "$LOG_FILE"
"$PROJECT_ROOT/.venv/bin/python" -u -m src.cli sync-ratings --no-dashboard >> "$LOG_FILE" 2>&1 || true

# Prevent system sleep during scraping using caffeinate
START_TS=$(date +%s)
caffeinate -i "$PROJECT_ROOT/.venv/bin/python" -u -m src.cli run --quick --limit 12 --force --push >> "$LOG_FILE" 2>&1
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
    echo "✅ [$(date '+%Y-%m-%d %H:%M:%S')] Daily Quick Scan completed successfully in $ELAPSED_FMT." >> "$LOG_FILE"
    osascript -e "display notification \"90-day quick market scan finished in $ELAPSED_FMT & dashboard updated.\" with title \"✅ Quick Scan Complete\" sound name \"Glass\"" 2>/dev/null || true
else
    echo "❌ [$(date '+%Y-%m-%d %H:%M:%S')] Daily Quick Scan failed after $ELAPSED_FMT with exit code $EXIT_CODE." >> "$LOG_FILE"
    osascript -e "display notification \"Market quick scan failed after $ELAPSED_FMT. Check daily_quickscan.log.\" with title \"❌ Quick Scan Failed\" sound name \"Basso\"" 2>/dev/null || true
fi

exit $EXIT_CODE

