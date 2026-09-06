#!/usr/bin/env bash
# ==============================================================================
# STR Price Advisor — macOS Launchd Installer
# Sets up, customizes, and activates the 3 scheduled automation LaunchAgents.
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs/str-price-advisor"
USER_ID="$(id -u)"

echo "================================================================="
echo "🚀 Setting up STR Price Advisor Launchd Daemons on macOS"
echo "================================================================="
echo "  User:         $USER (UID: $USER_ID)"
echo "  Project Root: $PROJECT_ROOT"
echo "  Logs Dir:     $LOG_DIR"
echo "  Agents Dir:   $LAUNCH_AGENTS_DIR"
echo "================================================================="

# 1. Ensure log directory and LaunchAgents directory exist
mkdir -p "$LOG_DIR"
mkdir -p "$LAUNCH_AGENTS_DIR"

# 2. Ensure shell scripts are executable
chmod +x "$SCRIPT_DIR/run_pms_sync.sh"
chmod +x "$SCRIPT_DIR/run_daily_quickscan.sh"
chmod +x "$SCRIPT_DIR/run_weekly_fullscan.sh"

# 3. Process and install each plist template
PLISTS=(
    "com.villasol.pms-sync.plist"
    "com.villasol.daily-quickscan.plist"
    "com.villasol.weekly-fullscan.plist"
)

for PLIST in "${PLISTS[@]}"; do
    SRC_PLIST="$SCRIPT_DIR/$PLIST"
    DEST_PLIST="$LAUNCH_AGENTS_DIR/$PLIST"
    LABEL="${PLIST%.plist}"

    echo "⚙️  Configuring $LABEL..."

    # Unload existing instance if currently registered
    launchctl bootout "gui/$USER_ID/$LABEL" 2>/dev/null || launchctl unload "$DEST_PLIST" 2>/dev/null || true

    # Substitute placeholders with real paths
    sed -e "s|__PROJECT_DIR__|$PROJECT_ROOT|g" \
        -e "s|__HOME__|$HOME|g" \
        "$SRC_PLIST" > "$DEST_PLIST"

    # Validate plist syntax
    plutil -lint "$DEST_PLIST" > /dev/null

    # Load agent into user GUI domain
    launchctl bootstrap "gui/$USER_ID" "$DEST_PLIST" 2>/dev/null || launchctl load -w "$DEST_PLIST"
    echo "  ✓ Installed and activated: $DEST_PLIST"
done

echo ""
echo "================================================================="
echo "✅ All LaunchAgents successfully installed and active!"
echo "================================================================="
echo ""
echo "📅 Active Automation Schedule:"
echo "  1. com.villasol.pms-sync:        Every day at 6:00 AM"
echo "  2. com.villasol.daily-quickscan: Every day at 6:15 AM"
echo "  3. com.villasol.weekly-fullscan: Sundays at 2:00 AM"
echo ""
echo "🔍 Inspection & Diagnostic Commands:"
echo "  - Check status: launchctl list | grep villasol"
echo "  - Stream PMS logs:        tail -f $LOG_DIR/pms_sync.log"
echo "  - Stream Quick Scan logs: tail -f $LOG_DIR/daily_quickscan.log"
echo "  - Stream Full Scan logs:  tail -f $LOG_DIR/weekly_fullscan.log"
echo "  - Run PMS Sync now:       bash $SCRIPT_DIR/run_pms_sync.sh"
echo "  - Run Quick Scan now:     bash $SCRIPT_DIR/run_daily_quickscan.sh"
echo "  - Uninstall all daemons:  bash $SCRIPT_DIR/uninstall_launchd.sh"
echo "================================================================="

