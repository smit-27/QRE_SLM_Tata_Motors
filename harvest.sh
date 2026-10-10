#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if [ ! -f .venv/bin/activate ]; then
    echo "Run setup.sh first."
    exit 1
fi
source .venv/bin/activate

echo "=== NHTSA ==="
python -m harvest.nhtsa --force

echo "=== eCFR ==="
python -m harvest.ecfr

echo "=== USPTO ==="
if [ -n "$USPTO_API_KEY" ]; then
    python -m harvest.uspto
else
    echo "USPTO_API_KEY not set - skipping patents."
    echo "Set it with:  export USPTO_API_KEY=your-key   then run this file again."
fi

echo "=== Report ==="
python -m harvest.report