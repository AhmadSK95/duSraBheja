#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f ".venv/bin/activate" ]]; then
  source ".venv/bin/activate"
fi

TARGET_DATE="${1:-}"
if [[ -n "$TARGET_DATE" ]]; then
  python -m src.collector.browser_activity sync --date "$TARGET_DATE"
else
  python -m src.collector.browser_activity sync
fi
