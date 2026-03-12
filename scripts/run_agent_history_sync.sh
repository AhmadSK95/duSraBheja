#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-sync_agent_history}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="${ROOT_DIR}/.venv/bin/python"
SERVER_USER="${SERVER_USER:-deployer}"
SERVER_HOST="${SERVER_HOST:-104.131.63.231}"
SERVER_SSH_KEY="${SERVER_SSH_KEY:-$HOME/.ssh/id_ed25519}"
REMOTE_APP_DIR="${REMOTE_APP_DIR:-/opt/dusrabheja}"
PREP_DIR="$(mktemp -d /tmp/dusrabheja-agent-history.XXXXXX)"
PARSE_PYTHON=""

case "${MODE}" in
  bootstrap_agent_history|sync_agent_history) ;;
  *)
    echo "Usage: $0 [bootstrap_agent_history|sync_agent_history]" >&2
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
    "${VENV_PYTHON}" -m src.collector.agent_history "${MODE}" --prepare-dir "${PREP_DIR}"
    return 0
  fi

  if command -v python3.12 >/dev/null 2>&1; then
    PARSE_PYTHON="$(command -v python3.12)"
    python3.12 -m src.collector.agent_history "${MODE}" --prepare-dir "${PREP_DIR}"
    return 0
  fi

  return 1
}

if ! run_prepare_native; then
  echo "Need Python 3.12+ to run agent-history sync." >&2
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

REQUEST_COUNT="$("${PARSE_PYTHON}" - <<'PY' "${PREP_DIR}/meta.json"
import json
import sys
from pathlib import Path
meta = json.loads(Path(sys.argv[1]).read_text())
print(meta["request_count"])
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

commit_state() {
  mkdir -p "$(dirname "${STATE_PATH}")"
  cp "${PREP_DIR}/next_state.json" "${STATE_PATH}"
}

post_sync_report() {
  local payload_file="$1"
  ssh -i "${SERVER_SSH_KEY}" "${SERVER_USER}@${SERVER_HOST}" \
    "cd '${REMOTE_APP_DIR}' && /usr/bin/bash -lc 'set -a && source .env && curl -sS -X POST -H \"Authorization: Bearer \$API_TOKEN\" -H \"Content-Type: application/json\" --data-binary @- http://127.0.0.1:8000/api/sync/report'" \
    < "${payload_file}"
}

if [[ "${ITEMS_SEEN}" == "0" ]]; then
  if [[ "${MODE}" == "bootstrap_agent_history" ]]; then
    "${PARSE_PYTHON}" - <<'PY' "${PREP_DIR}/meta.json" "${PREP_DIR}"
import json
import sys
from pathlib import Path
meta = json.loads(Path(sys.argv[1]).read_text())
prep_dir = Path(sys.argv[2])
for index, source in enumerate(meta["sources"]):
    payload = {
        "source_type": source["source_type"],
        "source_name": source["source_name"],
        "mode": meta["mode"],
        "status": "noop",
        "items_seen": 0,
        "items_imported": 0,
        "device_name": meta["device_name"],
        "metadata": {"bootstrap": True},
    }
    (prep_dir / f"report_{index}.json").write_text(json.dumps(payload))
PY
    for report in "${PREP_DIR}"/report_*.json; do
      post_sync_report "${report}"
    done
  fi
  commit_state
  echo '{"status":"noop","items_seen":0}'
  exit 0
fi

if [[ "${REQUEST_COUNT}" -gt 0 ]]; then
  for ((i=0; i<REQUEST_COUNT; i++)); do
    REQUEST_FILE="${PREP_DIR}/request_${i}.json"
    RESPONSE_FILE="${PREP_DIR}/response_${i}.json"
    "${PARSE_PYTHON}" - <<'PY' "${PREP_DIR}/requests.json" "${REQUEST_FILE}" "${i}"
import json
import sys
from pathlib import Path
requests = json.loads(Path(sys.argv[1]).read_text())
request = dict(requests[int(sys.argv[3])])
request["emit_sync_event"] = False
Path(sys.argv[2]).write_text(json.dumps(request))
PY

    ssh -i "${SERVER_SSH_KEY}" "${SERVER_USER}@${SERVER_HOST}" \
      "cd '${REMOTE_APP_DIR}' && /usr/bin/bash -lc 'set -a && source .env && curl -sS -X POST -H \"Authorization: Bearer \$API_TOKEN\" -H \"Content-Type: application/json\" --data-binary @- http://127.0.0.1:8000/api/ingest/collector'" \
      < "${REQUEST_FILE}" > "${RESPONSE_FILE}"
  done
fi

"${PARSE_PYTHON}" - <<'PY' "${PREP_DIR}/meta.json" "${PREP_DIR}/requests.json" "${PREP_DIR}"
import json
import sys
from pathlib import Path

meta = json.loads(Path(sys.argv[1]).read_text())
requests = json.loads(Path(sys.argv[2]).read_text())
prep_dir = Path(sys.argv[3])

source_seen = {source["source_type"]: source["items_seen"] for source in meta["sources"]}
source_names = {source["source_type"]: source["source_name"] for source in meta["sources"]}
source_imported = {key: 0 for key in source_seen}
projects_touched = {key: set() for key in source_seen}

for index, request in enumerate(requests):
    response_path = prep_dir / f"response_{index}.json"
    if not response_path.exists():
        continue
    response = json.loads(response_path.read_text())
    source_type = request["source_type"]
    source_imported[source_type] = source_imported.get(source_type, 0) + int(response.get("items_imported") or 0)
    for project in response.get("projects_touched") or []:
        projects_touched.setdefault(source_type, set()).add(project)

for index, source_type in enumerate(sorted(source_seen)):
    if meta["mode"] == "sync_agent_history" and source_seen[source_type] == 0:
        continue
    payload = {
        "source_type": source_type,
        "source_name": source_names[source_type],
        "mode": meta["mode"],
        "status": "completed",
        "items_seen": source_seen[source_type],
        "items_imported": source_imported.get(source_type, 0),
        "device_name": meta["device_name"],
        "metadata": {
            "projects_touched": sorted(projects_touched.get(source_type, set())),
        },
    }
    (prep_dir / f"report_{index}.json").write_text(json.dumps(payload))
PY

for report in "${PREP_DIR}"/report_*.json; do
  [[ -f "${report}" ]] || continue
  post_sync_report "${report}"
done

commit_state
