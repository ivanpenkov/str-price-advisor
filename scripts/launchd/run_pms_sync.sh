#!/usr/bin/env bash
# ==============================================================================
# Job 1: Daily Streamline OwnerX PMS Reservation Sync (6:00 AM)
# Ingests recent reservations, updates SQLite & JSON databases, and refreshes dashboard.
# Headless operation: completely silent background daemon with automated emergency git push.
# ==============================================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOG_DIR="$HOME/Library/Logs/str-price-advisor"
LOG_FILE="$LOG_DIR/pms_sync.log"
JOB_NAME="pms-sync"

mkdir -p "$LOG_DIR"

echo "=================================================================" >> "$LOG_FILE"
echo "⏰ [$(date '+%Y-%m-%d %H:%M:%S')] Starting Daily PMS Reservation Sync..." >> "$LOG_FILE"

cd "$PROJECT_ROOT" || exit 1

# Prevent concurrent execution of PMS reservation sync
# Uses kernel-level file descriptor lock (lockf/flock) to guarantee instant release on termination/crash.
LOCK_FILE="/tmp/villasol_pms_sync.lock"
exec 9>>"$LOCK_FILE"
if ! /usr/bin/lockf -s -t 0 9; then
    HOLDING_PID=$(cat "$LOCK_FILE" 2>/dev/null || echo "unknown")
    echo "⚠️ [$(date '+%Y-%m-%d %H:%M:%S')] Another instance of PMS Reservation Sync is currently running (PID: ${HOLDING_PID:-unknown}). Skipping this run to prevent parallel execution." >> "$LOG_FILE"
    exit 0
fi
echo "$$" > "$LOCK_FILE"

# Keep system awake against both idle sleep and DarkWake/Maintenance sleep on AC power (-sim)
caffeinate -sim -w $$ &
CAFFEINATE_PID=$!

cleanup() {
    if [ -n "${CAFFEINATE_PID:-}" ] && kill -0 "$CAFFEINATE_PID" 2>/dev/null; then
        kill "$CAFFEINATE_PID" 2>/dev/null || true
    fi
}

# Surgical Emergency Failure Handler
emergency_push_on_failure() {
    local exit_code="${1:-${EXIT_CODE:-1}}"
    if [ "$exit_code" -eq 0 ]; then
        exit_code=1
    fi
    local ts_pt
    ts_pt=$(TZ="America/Los_Angeles" date '+%Y-%m-%d %H:%M:%S %Z')
    echo "❌ [${ts_pt}] Emergency trap: ${JOB_NAME} failed with exit code ${exit_code}." >> "$LOG_FILE"

    # Surgical staging: ONLY run history and logs
    if [ -f "docs/data/run_history.json" ]; then
        git add docs/data/run_history.json >> "$LOG_FILE" 2>&1 || true
    fi
    if [ -d "docs/logs" ]; then
        git add docs/logs/*.txt >> "$LOG_FILE" 2>&1 || true
    fi

    # Check if run tracker or script staged changes
    if ! git diff --cached --quiet; then
        echo "🚨 Committing and pushing emergency failure record to GitHub Pages..." >> "$LOG_FILE"
        git commit -m "🚨 Automated Alert: ${JOB_NAME} failed with exit code ${exit_code} (${ts_pt})" >> "$LOG_FILE" 2>&1 || true
        if ! git pull --rebase -X theirs --autostash origin main >> "$LOG_FILE" 2>&1; then
            echo "⚠️ Rebase conflict encountered during emergency push. Aborting rebase to preserve clean tree." >> "$LOG_FILE"
            git rebase --abort >> "$LOG_FILE" 2>&1 || true
        else
            git push origin main >> "$LOG_FILE" 2>&1 || true
        fi
    fi

    cleanup
    exec 9>&- 2>/dev/null || true
    exit "$exit_code"
}

trap 'cleanup; exec 9>&- 2>/dev/null || true; exit 130' INT
trap 'cleanup; exec 9>&- 2>/dev/null || true; exit 143' TERM
trap cleanup EXIT

# Pre-flight repository sync: ensure local checkout has latest code and commits from origin/main
echo "🔄 Pre-flight repository sync with origin/main..." >> "$LOG_FILE"
if ! git pull --rebase -X theirs --autostash origin main >> "$LOG_FILE" 2>&1; then
    echo "⚠️ Pre-flight git pull encountered an issue. Aborting rebase to preserve clean tree." >> "$LOG_FILE"
    git rebase --abort >> "$LOG_FILE" 2>&1 || true
fi

# Prevent system sleep during execution using caffeinate
START_TS=$(date +%s)
EXIT_CODE=0
caffeinate -sim "$PROJECT_ROOT/.venv/bin/python" -u -m src.cli sync-reservations --days-back 60 --dashboard --push --trigger launchd >> "$LOG_FILE" 2>&1 || EXIT_CODE=$?
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
    echo "✅ [$(date '+%Y-%m-%d %H:%M:%S')] Daily PMS Sync completed successfully in $ELAPSED_FMT." >> "$LOG_FILE"
else
    ERR_DETAIL=$(tail -n 20 "$LOG_FILE" | grep -v "❌" | grep -E "Error|Exception|failed" | tail -n 2 | sed 's/^[[:blank:]]*//' | tr '\n' ' ' | sed 's/ $//' || true)
    echo "❌ [$(date '+%Y-%m-%d %H:%M:%S')] Daily PMS Sync failed after $ELAPSED_FMT with exit code $EXIT_CODE. Detail: ${ERR_DETAIL}" >> "$LOG_FILE"
    emergency_push_on_failure "$EXIT_CODE"
fi

# Release lock descriptor explicitly before script termination
exec 9>&- 2>/dev/null || true

exit $EXIT_CODE
