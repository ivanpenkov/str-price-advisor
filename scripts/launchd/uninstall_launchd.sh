#!/usr/bin/env bash
# ==============================================================================
# STR Price Advisor — macOS Launchd Uninstaller
# Unloads and removes the 3 scheduled automation LaunchAgents.
# ==============================================================================

set -uo pipefail

LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
USER_ID="$(id -u)"

PLISTS=(
    "com.villasol.pms-sync.plist"
    "com.villasol.daily-quickscan.plist"
    "com.villasol.weekly-fullscan.plist"
)

echo "================================================================="
echo "🗑️  Unloading STR Price Advisor Launchd Daemons"
echo "================================================================="

for PLIST in "${PLISTS[@]}"; do
    DEST_PLIST="$LAUNCH_AGENTS_DIR/$PLIST"
    LABEL="${PLIST%.plist}"

    echo "Stopping $LABEL..."
    launchctl bootout "gui/$USER_ID/$LABEL" 2>/dev/null || launchctl unload "$DEST_PLIST" 2>/dev/null || true

    if [ -f "$DEST_PLIST" ]; then
        rm -f "$DEST_PLIST"
        echo "  ✓ Removed $DEST_PLIST"
    fi
done

echo ""
echo "================================================================="
echo "✅ All STR Price Advisor LaunchAgents have been uninstalled."
echo "================================================================="

