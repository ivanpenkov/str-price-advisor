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

# Prevent concurrent execution of market scans (daily quickscan & weekly fullscan)
# Uses kernel-level file descriptor lock (lockf/flock) to guarantee instant release on termination/crash.
# Note: exec 9>> is deliberately used instead of 9> to avoid truncating the lockfile before lock acquisition.
LOCK_FILE="/tmp/villasol_market_scan.lock"
exec 9>>"$LOCK_FILE"
if ! /usr/bin/lockf -s -t 0 9; then
    HOLDING_PID=$(cat "$LOCK_FILE" 2>/dev/null || echo "unknown")
    echo "⚠️ [$(date '+%Y-%m-%d %H:%M:%S')] Another instance of STR Market Scan is currently running (PID: ${HOLDING_PID:-unknown}). Skipping this run to prevent parallel execution." >> "$LOG_FILE"
    exit 0
fi
echo "$$" > "$LOCK_FILE"

# Ensure stale child processes or forwarders from previous completed sessions are cleanly reaped
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

NOTIFY_MOBILE="$HOME/.gemini/config/scripts/notify_mobile.sh"

if [ $EXIT_CODE -eq 0 ]; then
    echo "✅ [$(date '+%Y-%m-%d %H:%M:%S')] Daily Quick Scan completed successfully in $ELAPSED_FMT." >> "$LOG_FILE"
    osascript -e "display notification \"90-day quick market scan finished in $ELAPSED_FMT & dashboard updated.\" with title \"✅ Quick Scan Complete\" sound name \"Glass\"" 2>/dev/null || true
    if [ -x "$NOTIFY_MOBILE" ]; then
        "$NOTIFY_MOBILE" \
            --title "✅ Daily Quick Scan Complete" \
            --message "90-day quick market scan finished in $ELAPSED_FMT & dashboard updated." \
            --tags "white_check_mark,rocket" 2>/dev/null || true
    fi
else
    # Extract actual error detail before writing the failure banner (exclude script banner lines)
    ERR_DETAIL=$(tail -n 20 "$LOG_FILE" | grep -v "❌" | grep -E "Error|RuntimeError|Exception|failed" | tail -n 2 | sed 's/^[[:blank:]]*//' | tr '\n' ' ' | sed 's/ $//' || true)
    echo "❌ [$(date '+%Y-%m-%d %H:%M:%S')] Daily Quick Scan failed after $ELAPSED_FMT with exit code $EXIT_CODE." >> "$LOG_FILE"

    # Send mobile push alert immediately
    if [ -x "$NOTIFY_MOBILE" ]; then
        MSG="Market quick scan failed after $ELAPSED_FMT (exit code $EXIT_CODE)."
        if [ -n "$ERR_DETAIL" ]; then
            MSG="$MSG Cause: $ERR_DETAIL"
        fi
        "$NOTIFY_MOBILE" \
            --title "❌ Daily Quick Scan Failed" \
            --message "$MSG" \
            --priority high \
            --tags "warning,x" 2>/dev/null || true
    fi

    # Display persistent desktop alert asynchronously in background.
    # CRITICAL: 9>&- explicitly closes FD 9 so the background UI process does not hold the kernel lock for up to 24h!
    ( osascript -e "display alert \"❌ Daily Quick Scan Failed\" message \"Market quick scan failed after $ELAPSED_FMT with exit code $EXIT_CODE. Check daily_quickscan.log.\" as critical giving up after 86400" 2>/dev/null || true ) 9>&- &
fi

# Release lock descriptor explicitly before script termination
exec 9>&- 2>/dev/null || true

exit $EXIT_CODE

