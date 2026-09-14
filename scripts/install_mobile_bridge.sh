#!/usr/bin/env bash
# ==============================================================================
# STR Price Advisor — Mobile ntfy Bridge Service Manager
# Installs, configures, starts, stops, and audits the mobile bridge LaunchAgent.
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs/str-price-advisor"
USER_ID="$(id -u)"
LABEL="com.villasol.mobile-ntfy-bridge"
SRC_PLIST="$SCRIPT_DIR/launchd/$LABEL.plist"
DEST_PLIST="$LAUNCH_AGENTS_DIR/$LABEL.plist"

ACTION="${1:---install}"

mkdir -p "$LOG_DIR"
mkdir -p "$LAUNCH_AGENTS_DIR"
chmod +x "$SCRIPT_DIR/mobile_ntfy_bridge.py"
chmod +x "$SCRIPT_DIR/launchd/run_mobile_bridge.sh"

case "$ACTION" in
    --install|install)
        echo "================================================================="
        echo "🚀 Installing Antigravity Mobile ntfy Bridge Service"
        echo "================================================================="
        echo "  User:         $USER (UID: $USER_ID)"
        echo "  Project Root: $PROJECT_ROOT"
        echo "  Logs Dir:     $LOG_DIR"
        echo "  Plist Dest:   $DEST_PLIST"
        echo "================================================================="

        # Unload any existing instance
        launchctl bootout "gui/$USER_ID/$LABEL" 2>/dev/null || launchctl unload "$DEST_PLIST" 2>/dev/null || true

        # Substitute placeholders
        sed -e "s|__PROJECT_DIR__|$PROJECT_ROOT|g" \
            -e "s|__HOME__|$HOME|g" \
            "$SRC_PLIST" > "$DEST_PLIST"

        plutil -lint "$DEST_PLIST" > /dev/null

        # Load agent into user GUI domain
        launchctl bootstrap "gui/$USER_ID" "$DEST_PLIST" 2>/dev/null || launchctl load -w "$DEST_PLIST"
        echo "  ✓ Installed and activated: $LABEL"
        echo ""
        echo "Status check:"
        launchctl list | grep "$LABEL" || true
        echo "================================================================="
        ;;

    --start|start)
        echo "Starting $LABEL..."
        launchctl kickstart -k "gui/$USER_ID/$LABEL" 2>/dev/null || launchctl start "$LABEL"
        echo "✓ Started"
        ;;

    --stop|stop)
        echo "Stopping $LABEL..."
        launchctl bootout "gui/$USER_ID/$LABEL" 2>/dev/null || launchctl stop "$LABEL" 2>/dev/null || true
        echo "✓ Stopped"
        ;;

    --status|status)
        echo "=== Status: $LABEL ==="
        if launchctl list | grep "$LABEL" >/dev/null 2>&1; then
            echo "Daemon is registered in launchd:"
            launchctl list | grep "$LABEL"
        else
            echo "Daemon is NOT registered in launchd."
        fi
        echo ""
        echo "=== Last 15 lines of log ($LOG_DIR/mobile_bridge.log) ==="
        if [[ -f "$LOG_DIR/mobile_bridge.log" ]]; then
            tail -n 15 "$LOG_DIR/mobile_bridge.log"
        else
            echo "(Log file does not exist yet)"
        fi
        ;;

    --uninstall|uninstall)
        echo "Uninstalling $LABEL..."
        launchctl bootout "gui/$USER_ID/$LABEL" 2>/dev/null || launchctl unload "$DEST_PLIST" 2>/dev/null || true
        rm -f "$DEST_PLIST"
        echo "✓ Uninstalled $LABEL"
        ;;

    --logs|logs)
        tail -f "$LOG_DIR/mobile_bridge.log" "$LOG_DIR/mobile_bridge.err"
        ;;

    *)
        echo "Usage: $0 [--install | --start | --stop | --status | --uninstall | --logs]"
        exit 1
        ;;
esac

