#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-sync}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="${ROOT_DIR}/.venv/bin/python"
DOCKER_IMAGE="${DOCKER_IMAGE:-dusrabheja-local-collector:latest}"
COLLECTOR_API_BASE_URL="${COLLECTOR_API_BASE_URL:-http://127.0.0.1:18000}"
DEFAULT_TUNNEL_PORT="${COLLECTOR_API_BASE_URL##*:}"
if [[ "${DEFAULT_TUNNEL_PORT}" == "${COLLECTOR_API_BASE_URL}" ]]; then
  DEFAULT_TUNNEL_PORT="18000"
fi
COLLECTOR_TUNNEL_PORT="${COLLECTOR_TUNNEL_PORT:-${DEFAULT_TUNNEL_PORT}}"
TUNNEL_PID=""

case "${MODE}" in
  bootstrap|sync) ;;
  *)
    echo "Usage: $0 [bootstrap|sync]" >&2
    exit 1
    ;;
esac

cd "${ROOT_DIR}"
export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-/tmp/dusrabheja-pycache}"

cleanup() {
  if [[ -n "${TUNNEL_PID}" ]]; then
    kill "${TUNNEL_PID}" >/dev/null 2>&1 || true
  fi
}

trap cleanup EXIT

if [[ "${COLLECTOR_API_BASE_URL}" == http://127.0.0.1:* || "${COLLECTOR_API_BASE_URL}" == http://localhost:* ]]; then
  LOCAL_PORT="${COLLECTOR_TUNNEL_PORT}" ./scripts/open_collector_tunnel.sh &
  TUNNEL_PID=$!
  sleep 2
fi

if [[ -x "${VENV_PYTHON}" ]] && "${VENV_PYTHON}" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'; then
  COLLECTOR_API_BASE_URL="${COLLECTOR_API_BASE_URL}" "${VENV_PYTHON}" -m src.collector.main "${MODE}"
  exit $?
fi

if command -v python3.12 >/dev/null 2>&1; then
  COLLECTOR_API_BASE_URL="${COLLECTOR_API_BASE_URL}" python3.12 -m src.collector.main "${MODE}"
  exit $?
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Need Python 3.12+ or Docker to run the collector." >&2
  exit 1
fi

if ! docker image inspect "${DOCKER_IMAGE}" >/dev/null 2>&1; then
  docker build -t "${DOCKER_IMAGE}" "${ROOT_DIR}"
fi

DOCKER_COLLECTOR_API_BASE_URL="${COLLECTOR_API_BASE_URL}"
if [[ "${DOCKER_COLLECTOR_API_BASE_URL}" == http://127.0.0.1:* || "${DOCKER_COLLECTOR_API_BASE_URL}" == http://localhost:* ]]; then
  DOCKER_COLLECTOR_API_BASE_URL="http://host.docker.internal:${COLLECTOR_TUNNEL_PORT}"
fi

docker run --rm \
  -v "${ROOT_DIR}:/app" \
  -w /app \
  --env-file "${ROOT_DIR}/.env" \
  -e PYTHONPYCACHEPREFIX=/tmp/dusrabheja-pycache \
  -e COLLECTOR_API_BASE_URL="${DOCKER_COLLECTOR_API_BASE_URL}" \
  "${DOCKER_IMAGE}" \
  python -m src.collector.main "${MODE}"
exit $?
