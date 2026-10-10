#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

MARKER=".done_nhtsa"
if [ -f "$MARKER" ]; then
    echo "✅ NHTSA already completed successfully on $(cat $MARKER) — nothing to do."
    echo "   (delete $MARKER if you really want to redo it)"
    exit 0
fi

if [ ! -f .venv/bin/activate ]; then echo "Run setup.sh first."; exit 1; fi
source .venv/bin/activate

echo "=== NHTSA ==="
python -m harvest.nhtsa --force

date -u +"%Y-%m-%d %H:%M UTC" > "$MARKER"
echo ""
echo "✅ SUCCESS — NHTSA harvest complete. Marker written to $MARKER."
echo "   You do NOT need to run this script again."