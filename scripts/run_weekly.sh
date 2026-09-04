#!/usr/bin/env bash
# STR Competitive Price Advisor - Automated Weekly Runner
# Location: /Users/ivanpe/str-price-advisor/scripts/run_weekly.sh

set -euo pipefail

# Ensure standard environment and tools are available (homebrew, system binaries)
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

MODE="${1:---quick}"  # Default to --quick (first 15 intervals ~5-8 mins), or pass --weekly for full sweep

echo "========================================================"
echo "🕒 [$(date '+%Y-%m-%d %H:%M:%S')] Starting STR Price Advisor Run ($MODE)"
echo "📁 Project Directory: $PROJECT_DIR"
echo "========================================================"

# Activate virtual environment
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
else
    echo "❌ Error: Virtual environment .venv not found in $PROJECT_DIR"
    exit 1
fi

# Run the pricing advisor
if [ "$MODE" = "--weekly" ] || [ "$MODE" = "--full" ]; then
    echo "🚀 Running full 12-month calendar scan..."
    python -m src.cli run --weekly
else
    echo "⚡ Running quick scan on upcoming intervals..."
    python -m src.cli run --quick --limit 15
fi

# Commit and push updated dashboard to GitHub Pages if there are changes
if [ -n "$(git status --porcelain docs/ data/)" ]; then
    echo "📤 Committing and pushing updated dashboard and data to GitHub..."
    git add docs/ data/
    git commit -m "chore: automated price advisory update [$(date '+%Y-%m-%d')]"
    git push origin main || {
        echo "⚠️ Git push failed or required authentication. Changes committed locally."
    }
    echo "✅ Dashboard published to GitHub Pages!"
else
    echo "ℹ️ No changes detected in docs/ or data/."
fi

echo "🎉 [$(date '+%Y-%m-%d %H:%M:%S')] Run complete!"
