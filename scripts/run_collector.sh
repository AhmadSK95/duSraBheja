#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-sync}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="${ROOT_DIR}/.venv/bin/python"

case "${MODE}" in
  bootstrap|sync) ;;
  *)
    echo "Usage: $0 [bootstrap|sync]" >&2
    exit 1
    ;;
esac

if [[ -x "${VENV_PYTHON}" ]]; then
  PYTHON_BIN="${VENV_PYTHON}"
else
  PYTHON_BIN="$(command -v python3 || true)"
fi

if [[ -z "${PYTHON_BIN}" ]]; then
  echo "python3 not found. Create .venv or install Python 3." >&2
  exit 1
fi

cd "${ROOT_DIR}"
export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-/tmp/dusrabheja-pycache}"

exec "${PYTHON_BIN}" -m src.collector.main "${MODE}"
