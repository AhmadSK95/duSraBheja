#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f ".venv/bin/activate" ]]; then
  source ".venv/bin/activate"
fi

TARGET_DATE="${1:-}"
PROFILE_EMAIL="${CHROME_SIGNAL_PROFILE_EMAIL:-ahmad2609.as@gmail.com}"

if [[ -n "$TARGET_DATE" ]]; then
  python -m src.collector.chrome_signals ingest --profile-email "$PROFILE_EMAIL" --mode daily --date "$TARGET_DATE"
else
  python -m src.collector.chrome_signals ingest --profile-email "$PROFILE_EMAIL" --mode daily
fi
