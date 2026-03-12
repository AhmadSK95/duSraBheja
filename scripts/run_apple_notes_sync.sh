#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-sync}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="${ROOT_DIR}/.venv/bin/python"
SERVER_USER="${SERVER_USER:-deployer}"
SERVER_HOST="${SERVER_HOST:-104.131.63.231}"
SERVER_SSH_KEY="${SERVER_SSH_KEY:-$HOME/.ssh/id_ed25519}"
REMOTE_APP_DIR="${REMOTE_APP_DIR:-/opt/dusrabheja}"
PREP_DIR="$(mktemp -d /tmp/dusrabheja-apple-notes.XXXXXX)"
PARSE_PYTHON=""

case "${MODE}" in
  bootstrap|sync) ;;
  *)
    echo "Usage: $0 [bootstrap|sync]" >&2
    exit 1
    ;;
esac

cleanup() {
  rm -rf "${PREP_DIR}"
}

trap cleanup EXIT

cd "${ROOT_DIR}"
export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-/tmp/dusrabheja-pycache}"

run_prepare_native() {
  if [[ -x "${VENV_PYTHON}" ]] && "${VENV_PYTHON}" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'; then
    PARSE_PYTHON="${VENV_PYTHON}"
    "${VENV_PYTHON}" -m src.collector.apple_notes "${MODE}" --prepare-dir "${PREP_DIR}"
    return 0
  fi

  if command -v python3.12 >/dev/null 2>&1; then
    PARSE_PYTHON="$(command -v python3.12)"
    python3.12 -m src.collector.apple_notes "${MODE}" --prepare-dir "${PREP_DIR}"
    return 0
  fi

  return 1
}

if ! run_prepare_native; then
  echo "Need Python 3.12+ to run Apple Notes sync." >&2
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

STATE_PATH="$("${PARSE_PYTHON}" - <<'PY' "${PREP_DIR}/meta.json"
import json
import sys
from pathlib import Path
meta = json.loads(Path(sys.argv[1]).read_text())
print(meta["state_path"])
PY
)"

DEVICE_NAME="$("${PARSE_PYTHON}" - <<'PY' "${PREP_DIR}/meta.json"
import json
import sys
from pathlib import Path
meta = json.loads(Path(sys.argv[1]).read_text())
print(meta["device_name"])
PY
)"

SOURCE_NAME="$("${PARSE_PYTHON}" - <<'PY' "${PREP_DIR}/meta.json"
import json
import sys
from pathlib import Path
meta = json.loads(Path(sys.argv[1]).read_text())
print(meta["source_name"])
PY
)"

commit_state() {
  mkdir -p "$(dirname "${STATE_PATH}")"
  cp "${PREP_DIR}/next_state.json" "${STATE_PATH}"
}

if [[ "${ITEMS_SEEN}" == "0" ]]; then
  ssh -i "${SERVER_SSH_KEY}" "${SERVER_USER}@${SERVER_HOST}" \
    "cd '${REMOTE_APP_DIR}' && /usr/bin/bash -lc 'set -a && source .env && curl -sS -X POST -H \"Authorization: Bearer \$API_TOKEN\" -H \"Content-Type: application/json\" --data-binary @- http://127.0.0.1:8000/api/sync/report'" \
    <<EOF
{"source_type":"apple_notes","source_name":"${SOURCE_NAME}","mode":"${MODE}","status":"noop","items_seen":0,"items_imported":0,"device_name":"${DEVICE_NAME}"}
EOF
  commit_state
  echo '{"status":"noop","items_seen":0}'
  exit 0
fi

ssh -i "${SERVER_SSH_KEY}" "${SERVER_USER}@${SERVER_HOST}" \
  "cd '${REMOTE_APP_DIR}' && /usr/bin/bash -lc 'set -a && source .env && curl -sS -X POST -H \"Authorization: Bearer \$API_TOKEN\" -H \"Content-Type: application/json\" --data-binary @- http://127.0.0.1:8000/api/ingest/collector'" \
  < "${PREP_DIR}/payload.json"

commit_state
