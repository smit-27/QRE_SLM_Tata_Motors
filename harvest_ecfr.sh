#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

MARKER=".done_ecfr"
if [ -f "$MARKER" ]; then
    echo "✅ eCFR already completed successfully on $(cat $MARKER) — nothing to do."
    echo "   (delete $MARKER if you really want to redo it)"
    exit 0
fi

if [ ! -f .venv/bin/activate ]; then echo "Run setup.sh first."; exit 1; fi
source .venv/bin/activate

echo "=== eCFR ==="
python -m harvest.ecfr

date -u +"%Y-%m-%d %H:%M UTC" > "$MARKER"
echo ""
echo "✅ SUCCESS — eCFR harvest complete. Marker written to $MARKER."
echo "   You do NOT need to run this script again."