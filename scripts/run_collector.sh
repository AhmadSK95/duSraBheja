#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-sync}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="${ROOT_DIR}/.venv/bin/python"
DOCKER_IMAGE="${DOCKER_IMAGE:-dusrabheja-local-collector:latest}"

case "${MODE}" in
  bootstrap|sync) ;;
  *)
    echo "Usage: $0 [bootstrap|sync]" >&2
    exit 1
    ;;
esac

cd "${ROOT_DIR}"
export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-/tmp/dusrabheja-pycache}"

if [[ -x "${VENV_PYTHON}" ]] && "${VENV_PYTHON}" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'; then
  exec "${VENV_PYTHON}" -m src.collector.main "${MODE}"
fi

if command -v python3.12 >/dev/null 2>&1; then
  exec python3.12 -m src.collector.main "${MODE}"
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Need Python 3.12+ or Docker to run the collector." >&2
  exit 1
fi

if ! docker image inspect "${DOCKER_IMAGE}" >/dev/null 2>&1; then
  docker build -t "${DOCKER_IMAGE}" "${ROOT_DIR}"
fi

COLLECTOR_API_BASE_URL="${COLLECTOR_API_BASE_URL:-http://127.0.0.1:8000}"
if [[ "${COLLECTOR_API_BASE_URL}" == "http://127.0.0.1:8000" ]]; then
  COLLECTOR_API_BASE_URL="http://host.docker.internal:8000"
fi

exec docker run --rm \
  -v "${ROOT_DIR}:/app" \
  -w /app \
  --env-file "${ROOT_DIR}/.env" \
  -e PYTHONPYCACHEPREFIX=/tmp/dusrabheja-pycache \
  -e COLLECTOR_API_BASE_URL="${COLLECTOR_API_BASE_URL}" \
  "${DOCKER_IMAGE}" \
  python -m src.collector.main "${MODE}"
