#!/data/data/com.termux/files/usr/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
    echo "[ERROR] python3 was not found. Run: pkg install python"
    exit 1
fi

python3 "$SCRIPT_DIR/run_pipeline.py" "$@"
