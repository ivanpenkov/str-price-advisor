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

NOTIFY_MOBILE="$HOME/.gemini/config/scripts/notify_mobile.sh"

if [ $EXIT_CODE -eq 0 ]; then
    echo "✅ [$(date '+%Y-%m-%d %H:%M:%S')] Weekly Full Scan completed successfully in $ELAPSED_FMT." >> "$LOG_FILE"
    osascript -e "display notification \"Full 12-month market scan & channel comparison complete in $ELAPSED_FMT.\" with title \"✅ Weekly Audit Complete\" sound name \"Glass\"" 2>/dev/null || true
    if [ -x "$NOTIFY_MOBILE" ]; then
        "$NOTIFY_MOBILE" \
            --title "✅ Weekly Full Scan Complete" \
            --message "Full 12-month market scan & channel comparison complete in $ELAPSED_FMT." \
            --tags "white_check_mark,rocket" 2>/dev/null || true
    fi
else
    # Extract actual error detail before writing the failure banner (exclude script banner lines)
    ERR_DETAIL=$(tail -n 20 "$LOG_FILE" | grep -v "❌" | grep -E "Error|RuntimeError|Exception|failed" | tail -n 2 | sed 's/^[[:blank:]]*//' | tr '\n' ' ' | sed 's/ $//' || true)
    echo "❌ [$(date '+%Y-%m-%d %H:%M:%S')] Weekly Full Scan failed after $ELAPSED_FMT with exit code $EXIT_CODE." >> "$LOG_FILE"

    # Send mobile push alert immediately
    if [ -x "$NOTIFY_MOBILE" ]; then
        MSG="Weekly full scan failed after $ELAPSED_FMT (exit code $EXIT_CODE)."
        if [ -n "$ERR_DETAIL" ]; then
            MSG="$MSG Cause: $ERR_DETAIL"
        fi
        "$NOTIFY_MOBILE" \
            --title "❌ Weekly Full Scan Failed" \
            --message "$MSG" \
            --priority high \
            --tags "warning,x" 2>/dev/null || true
    fi

    # Display persistent desktop alert asynchronously in background.
    # CRITICAL: 9>&- explicitly closes FD 9 so the background UI process does not hold the kernel lock for up to 24h!
    ( osascript -e "display alert \"❌ Weekly Full Scan Failed\" message \"Weekly full scan failed after $ELAPSED_FMT with exit code $EXIT_CODE. Check weekly_fullscan.log.\" as critical giving up after 86400" 2>/dev/null || true ) 9>&- &
fi

# Release lock descriptor explicitly before script termination
exec 9>&- 2>/dev/null || true

exit $EXIT_CODE

