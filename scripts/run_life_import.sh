#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-bootstrap}"
if [[ "${MODE}" != "bootstrap" && "${MODE}" != "sync" ]]; then
  echo "Usage: $0 [bootstrap|sync] [--skip-apple-notes] [life export args...]" >&2
  exit 1
fi
shift || true

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="${ROOT_DIR}/.venv/bin/python"
SERVER_USER="${SERVER_USER:-deployer}"
SERVER_HOST="${SERVER_HOST:-104.131.63.231}"
SERVER_SSH_KEY="${SERVER_SSH_KEY:-$HOME/.ssh/id_ed25519}"
REMOTE_APP_DIR="${REMOTE_APP_DIR:-/opt/dusrabheja}"
PREP_DIR="$(mktemp -d /tmp/dusrabheja-life-import.XXXXXX)"
PARSE_PYTHON=""
RUN_APPLE_NOTES=1

if [[ "${1:-}" == "--skip-apple-notes" ]]; then
  RUN_APPLE_NOTES=0
  shift
fi

cleanup() {
  rm -rf "${PREP_DIR}"
}

trap cleanup EXIT

cd "${ROOT_DIR}"
export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-/tmp/dusrabheja-pycache}"

run_prepare_native() {
  if [[ -x "${VENV_PYTHON}" ]] && "${VENV_PYTHON}" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'; then
    PARSE_PYTHON="${VENV_PYTHON}"
    "${VENV_PYTHON}" -m src.collector.life_exports "${MODE}" --prepare-dir "${PREP_DIR}" "$@"
    return 0
  fi

  if command -v python3.12 >/dev/null 2>&1; then
    PARSE_PYTHON="$(command -v python3.12)"
    python3.12 -m src.collector.life_exports "${MODE}" --prepare-dir "${PREP_DIR}" "$@"
    return 0
  fi

  return 1
}

if [[ "${RUN_APPLE_NOTES}" == "1" ]]; then
  "${ROOT_DIR}/scripts/run_apple_notes_sync.sh" "${MODE}"
fi

if ! run_prepare_native "$@"; then
  echo "Need Python 3.12+ to run life import." >&2
  exit 1
fi

ITEMS_SEEN="$("${PARSE_PYTHON}" - <<'PY' "${PREP_DIR}/meta.json"
import json
import sys
from pathlib import Path
meta = json.loads(Path(sys.argv[1]).read_text())
print(meta["items_seen"])
PY
)"

if [[ "${ITEMS_SEEN}" == "0" ]]; then
  echo '{"status":"noop","items_seen":0}'
  exit 0
fi

while IFS= read -r payload_path; do
  ssh -i "${SERVER_SSH_KEY}" "${SERVER_USER}@${SERVER_HOST}" \
    "cd '${REMOTE_APP_DIR}' && /usr/bin/bash -lc 'set -a && source .env && curl -sS -X POST -H \"Authorization: Bearer \$API_TOKEN\" -H \"Content-Type: application/json\" --data-binary @- http://127.0.0.1:8000/api/ingest/collector'" \
    < "${payload_path}"
done < <(find "${PREP_DIR}/payloads" -type f -name '*.json' | sort)

cat "${PREP_DIR}/meta.json"
