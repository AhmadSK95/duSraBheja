#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-sync}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="${ROOT_DIR}/.venv/bin/python"
DOCKER_IMAGE="${DOCKER_IMAGE:-dusrabheja-local-collector:latest}"
SERVER_USER="${SERVER_USER:-deployer}"
SERVER_HOST="${SERVER_HOST:-104.131.63.231}"
SERVER_SSH_KEY="${SERVER_SSH_KEY:-$HOME/.ssh/id_ed25519}"
REMOTE_APP_DIR="${REMOTE_APP_DIR:-/opt/dusrabheja}"
PREP_DIR="$(mktemp -d /tmp/dusrabheja-collector.XXXXXX)"
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
    "${VENV_PYTHON}" -m src.collector.main "${MODE}" --prepare-dir "${PREP_DIR}"
    return 0
  fi

  if command -v python3.12 >/dev/null 2>&1; then
    PARSE_PYTHON="$(command -v python3.12)"
    python3.12 -m src.collector.main "${MODE}" --prepare-dir "${PREP_DIR}"
    return 0
  fi

  return 1
}

run_prepare_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "Need Python 3.12+ or Docker to run the collector." >&2
    exit 1
  fi

  if ! docker image inspect "${DOCKER_IMAGE}" >/dev/null 2>&1; then
    docker build -t "${DOCKER_IMAGE}" "${ROOT_DIR}"
  fi

  docker run --rm \
    -v "${ROOT_DIR}:/app" \
    -v "${PREP_DIR}:${PREP_DIR}" \
    -w /app \
    --env-file "${ROOT_DIR}/.env" \
    -e PYTHONPYCACHEPREFIX=/tmp/dusrabheja-pycache \
    "${DOCKER_IMAGE}" \
    python -m src.collector.main "${MODE}" --prepare-dir "${PREP_DIR}"
}

if ! run_prepare_native; then
  run_prepare_docker
fi

if [[ -z "${PARSE_PYTHON}" ]]; then
  if [[ -x "${VENV_PYTHON}" ]]; then
    PARSE_PYTHON="${VENV_PYTHON}"
  else
    PARSE_PYTHON="$(command -v python3.12)"
  fi
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

run_chrome_signal_sync() {
  if [[ "${MODE}" != "sync" ]]; then
    return 0
  fi
  if [[ ! -x "${ROOT_DIR}/scripts/run_chrome_signal_sync.sh" ]]; then
    return 0
  fi
  if ! "${ROOT_DIR}/scripts/run_chrome_signal_sync.sh"; then
    echo "Chrome signal sync failed; collector sync still completed." >&2
  fi
}

if [[ "${ITEMS_SEEN}" == "0" ]]; then
  ssh -i "${SERVER_SSH_KEY}" "${SERVER_USER}@${SERVER_HOST}" \
    "cd '${REMOTE_APP_DIR}' && /usr/bin/bash -lc 'set -a && source .env && curl -sS -X POST -H \"Authorization: Bearer \$API_TOKEN\" -H \"Content-Type: application/json\" --data-binary @- http://127.0.0.1:8000/api/sync/report'" \
    <<EOF
{"source_type":"collector","source_name":"${SOURCE_NAME}","mode":"${MODE}","status":"noop","items_seen":0,"items_imported":0,"device_name":"${DEVICE_NAME}"}
EOF
  commit_state
  run_chrome_signal_sync
  echo '{"status":"noop","items_seen":0}'
  exit 0
fi

ssh -i "${SERVER_SSH_KEY}" "${SERVER_USER}@${SERVER_HOST}" \
  "cd '${REMOTE_APP_DIR}' && /usr/bin/bash -lc 'set -a && source .env && curl -sS -X POST -H \"Authorization: Bearer \$API_TOKEN\" -H \"Content-Type: application/json\" --data-binary @- http://127.0.0.1:8000/api/ingest/collector'" \
  < "${PREP_DIR}/payload.json"

commit_state
run_chrome_signal_sync
