#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_PYTHON="${ROOT_DIR}/.venv/bin/python"
SERVER_USER="${SERVER_USER:-deployer}"
SERVER_HOST="${SERVER_HOST:-104.131.63.231}"
SERVER_SSH_KEY="${SERVER_SSH_KEY:-$HOME/.ssh/id_ed25519}"
REMOTE_APP_DIR="${REMOTE_APP_DIR:-/opt/dusrabheja}"
PREP_DIR="$(mktemp -d /tmp/dusrabheja-chrome.XXXXXX)"
TARGET_DATE="${1:-}"
PROFILE_EMAIL="${CHROME_SIGNAL_PROFILE_EMAIL:-ahmad2609.as@gmail.com}"
PARSE_PYTHON=""

cleanup() {
  rm -rf "${PREP_DIR}"
}

trap cleanup EXIT

cd "$ROOT_DIR"

run_prepare_native() {
  if [[ -x "${VENV_PYTHON}" ]] && "${VENV_PYTHON}" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'; then
    PARSE_PYTHON="${VENV_PYTHON}"
  elif command -v python3.12 >/dev/null 2>&1; then
    PARSE_PYTHON="$(command -v python3.12)"
  else
    echo "Need Python 3.12+ to run Chrome signal sync." >&2
    exit 1
  fi

  PROFILE_EMAIL="${PROFILE_EMAIL}" TARGET_DATE="${TARGET_DATE}" PREP_DIR="${PREP_DIR}" "${PARSE_PYTHON}" - <<'PY'
import asyncio
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

from src.collector.chrome_signals import prepare_entries
from src.config import settings


async def main() -> None:
    target_raw = os.environ.get("TARGET_DATE", "").strip()
    target_date = date.fromisoformat(target_raw) if target_raw else (datetime.now().astimezone().date() - timedelta(days=1))
    entries, preview = await prepare_entries(
        profile_email=os.environ["PROFILE_EMAIL"],
        profile_name=settings.chrome_signal_profile_name,
        mode="daily",
        target_date=target_date,
    )
    payload = {
        "source_type": "chrome_activity",
        "source_name": f"mac-chrome-{preview['profile']['directory'].lower().replace(' ', '-')}",
        "mode": "daily",
        "device_name": settings.collector_device_name,
        "emit_sync_event": True,
        "entries": entries,
    }
    prep_dir = Path(os.environ["PREP_DIR"])
    prep_dir.mkdir(parents=True, exist_ok=True)
    (prep_dir / "payload.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    (prep_dir / "preview.json").write_text(json.dumps(preview, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"target_date": target_date.isoformat(), "entries": len(entries)}))


asyncio.run(main())
PY
}

run_prepare_native

ssh -i "${SERVER_SSH_KEY}" "${SERVER_USER}@${SERVER_HOST}" \
  "cd '${REMOTE_APP_DIR}' && /usr/bin/bash -lc 'set -a && source .env && curl -sS -X POST -H \"Authorization: Bearer \$API_TOKEN\" -H \"Content-Type: application/json\" --data-binary @- http://127.0.0.1:8000/api/ingest/collector'" \
  < "${PREP_DIR}/payload.json"
