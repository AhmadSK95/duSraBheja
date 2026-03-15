#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

TARGET_DATE="${1:-}"
exec "$ROOT_DIR/scripts/run_chrome_signal_sync.sh" "$TARGET_DATE"
