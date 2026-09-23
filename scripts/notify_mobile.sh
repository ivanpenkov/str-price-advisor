#!/usr/bin/env bash
#
# Mobile Push Notification Dispatcher for Antigravity Agent
# Sends instant push notifications to the user's mobile device via ntfy.sh
#

set -euo pipefail

# Default configuration
DEFAULT_TOPIC="ivan-str-advisor-xyz"
TOPIC="${NTFY_TOPIC:-$DEFAULT_TOPIC}"
TITLE="Antigravity: Task Complete"
MESSAGE="Task execution completed. Ready for review at your desk."
PRIORITY="default"
TAGS="white_check_mark,rocket"
CLICK_URL=""

# Parse command line arguments
while [[ $# -gt 0 ]]; do
  case "$1" in
    --topic|-t)
      TOPIC="$2"
      shift 2
      ;;
    --title)
      TITLE="$2"
      shift 2
      ;;
    --message|-m)
      MESSAGE="$2"
      MESSAGE_SET=1
      shift 2
      ;;
    --priority|-p)
      PRIORITY="$2"
      shift 2
      ;;
    --tags)
      TAGS="$2"
      shift 2
      ;;
    --url|--click)
      CLICK_URL="$2"
      shift 2
      ;;
    --help|-h)
      echo "Usage: $0 [--title TITLE] [--message MSG] [--priority PRIORITY] [--tags TAGS] [--url URL] [MESSAGE]"
      exit 0
      ;;
    *)
      # If first unnamed argument, treat as message
      if [[ -z "${MESSAGE_SET:-}" ]]; then
        MESSAGE="$1"
        MESSAGE_SET=1
        shift
      else
        shift
      fi
      ;;
  esac
done

# Build curl headers
CURL_ARGS=(
  -s
  --fail
  --max-time 10
  -X POST
  -H "Title: $TITLE"
  -H "Priority: $PRIORITY"
  -H "Tags: $TAGS"
)

if [[ -n "$CLICK_URL" ]]; then
  CURL_ARGS+=(-H "Click: $CLICK_URL")
fi

CURL_ARGS+=(--data-raw "$MESSAGE")
CURL_ARGS+=("https://ntfy.sh/$TOPIC")

# Send notification (fail gracefully without breaking agent workflow)
if curl "${CURL_ARGS[@]}" > /dev/null 2>&1; then
  echo "✓ Push notification sent to ntfy.sh/$TOPIC"
else
  echo "⚠️ Warning: Failed to deliver push notification to ntfy.sh/$TOPIC" >&2
fi

