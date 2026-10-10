#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if [ ! -f .venv/bin/activate ]; then echo "Run setup.sh first."; exit 1; fi
source .venv/bin/activate

if [ -z "$USPTO_API_KEY" ]; then
    echo "USPTO_API_KEY not set. Run:  export USPTO_API_KEY=your-key"
    exit 1
fi

echo "=== USPTO ==="
python -m harvest.uspto

TOTAL=$(python -m harvest.uspto --list-only 2>/dev/null | wc -l)
DONE=$(wc -l < data/raw/uspto/_done.txt 2>/dev/null || echo 0)

echo ""
if [ "$DONE" -ge "$TOTAL" ] && [ "$TOTAL" -gt 0 ]; then
    date -u +"%Y-%m-%d %H:%M UTC" > .done_uspto
    echo "✅ SUCCESS — USPTO fully complete ($DONE/$TOTAL files). Marker written to .done_uspto."
    echo "   You do NOT need to run this script again."
else
    echo "⏳ USPTO in progress: $DONE/$TOTAL files done. Re-run this script to continue."
fi