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

# Prevent concurrent execution of PMS reservation sync
# Uses kernel-level file descriptor lock (lockf/flock) to guarantee instant release on termination/crash.
# Note: exec 9>> is deliberately used instead of 9> to avoid truncating the lockfile before lock acquisition.
LOCK_FILE="/tmp/villasol_pms_sync.lock"
exec 9>>"$LOCK_FILE"
if ! /usr/bin/lockf -s -t 0 9; then
    HOLDING_PID=$(cat "$LOCK_FILE" 2>/dev/null || echo "unknown")
    echo "⚠️ [$(date '+%Y-%m-%d %H:%M:%S')] Another instance of PMS Reservation Sync is currently running (PID: ${HOLDING_PID:-unknown}). Skipping this run to prevent parallel execution." >> "$LOG_FILE"
    exit 0
fi
echo "$$" > "$LOCK_FILE"

# Prevent system sleep during execution using caffeinate
START_TS=$(date +%s)
caffeinate -i "$PROJECT_ROOT/.venv/bin/python" -u -m src.cli sync-reservations --days-back 60 --dashboard --push >> "$LOG_FILE" 2>&1
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
    echo "✅ [$(date '+%Y-%m-%d %H:%M:%S')] Daily PMS Sync completed successfully in $ELAPSED_FMT." >> "$LOG_FILE"
    osascript -e 'display notification "Villa del Sol reservations synced & pushed to GitHub." with title "✅ PMS Sync Complete" sound name "Glass"' 2>/dev/null || true
    if [ -x "$NOTIFY_MOBILE" ]; then
        "$NOTIFY_MOBILE" \
            --title "✅ Daily PMS Sync Complete" \
            --message "Villa del Sol reservations synced & pushed to GitHub in $ELAPSED_FMT." \
            --tags "white_check_mark" 2>/dev/null || true
    fi
else
    # Extract actual error detail before writing the failure banner (exclude script banner lines)
    ERR_DETAIL=$(tail -n 20 "$LOG_FILE" | grep -v "❌" | grep -E "Error|Exception|failed" | tail -n 2 | sed 's/^[[:blank:]]*//' | tr '\n' ' ' | sed 's/ $//' || true)
    echo "❌ [$(date '+%Y-%m-%d %H:%M:%S')] Daily PMS Sync failed after $ELAPSED_FMT with exit code $EXIT_CODE." >> "$LOG_FILE"

    # Send mobile push alert immediately
    if [ -x "$NOTIFY_MOBILE" ]; then
        MSG="Daily PMS Sync failed after $ELAPSED_FMT (exit code $EXIT_CODE)."
        if [ -n "$ERR_DETAIL" ]; then
            MSG="$MSG Cause: $ERR_DETAIL"
        fi
        "$NOTIFY_MOBILE" \
            --title "❌ Daily PMS Sync Failed" \
            --message "$MSG" \
            --priority high \
            --tags "warning,x" 2>/dev/null || true
    fi

    # Display persistent desktop alert asynchronously in background.
    # CRITICAL: 9>&- explicitly closes FD 9 so the background UI process does not hold the kernel lock for up to 24h!
    ( osascript -e "display alert \"❌ PMS Sync Failed\" message \"Error syncing Villa del Sol reservations after $ELAPSED_FMT with exit code $EXIT_CODE. Check pms_sync.log.\" as critical giving up after 86400" 2>/dev/null || true ) 9>&- &
fi

# Release lock descriptor explicitly before script termination
exec 9>&- 2>/dev/null || true

exit $EXIT_CODE

