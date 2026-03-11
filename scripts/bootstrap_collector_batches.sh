#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-run}"

case "${MODE}" in
  run|--plan) ;;
  *)
    echo "Usage: $0 [run|--plan]" >&2
    exit 1
    ;;
esac

if [[ -f "${ROOT_DIR}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${ROOT_DIR}/.env"
  set +a
fi

DISCOVERY_ROOTS="${COLLECTOR_BOOTSTRAP_ROOTS:-${COLLECTOR_PROJECT_ROOTS:-}}"
if [[ -z "${DISCOVERY_ROOTS}" ]]; then
  echo "Set COLLECTOR_BOOTSTRAP_ROOTS or COLLECTOR_PROJECT_ROOTS in .env first." >&2
  exit 1
fi

if [[ -x "${ROOT_DIR}/.venv/bin/python" ]]; then
  PARSE_PYTHON="${ROOT_DIR}/.venv/bin/python"
elif command -v python3.12 >/dev/null 2>&1; then
  PARSE_PYTHON="$(command -v python3.12)"
else
  echo "Need project .venv or python3.12 to discover collector batches." >&2
  exit 1
fi

BATCH_ROOTS=()
while IFS= read -r root; do
  BATCH_ROOTS+=("${root}")
done < <(
  "${PARSE_PYTHON}" - <<'PY'
import os

from src.collector.main import discover_context_workspaces, discover_repo_roots, parse_paths

scan_depth = int(os.environ.get("COLLECTOR_SCAN_MAX_DEPTH", "4"))
discovery_roots = parse_paths(os.environ.get("COLLECTOR_BOOTSTRAP_ROOTS") or os.environ.get("COLLECTOR_PROJECT_ROOTS"))
inventory_roots = parse_paths(os.environ.get("COLLECTOR_BOOTSTRAP_INVENTORY_ROOTS"))

repo_roots = discover_repo_roots(discovery_roots, scan_depth)
workspace_roots = discover_context_workspaces(discovery_roots, repo_roots, scan_depth)

ordered = []
seen = set()
for path in [*repo_roots, *workspace_roots, *inventory_roots]:
    path_str = str(path)
    if path_str in seen:
        continue
    seen.add(path_str)
    ordered.append(path_str)

for path in ordered:
    print(path)
PY
)

if [[ "${#BATCH_ROOTS[@]}" -eq 0 ]]; then
  echo "No bootstrap roots discovered." >&2
  exit 1
fi

printf 'Bootstrap batches (%s):\n' "${#BATCH_ROOTS[@]}"
for root in "${BATCH_ROOTS[@]}"; do
  printf '  %s\n' "${root}"
done

if [[ "${MODE}" == "--plan" ]]; then
  exit 0
fi

for index in "${!BATCH_ROOTS[@]}"; do
  root="${BATCH_ROOTS[$index]}"
  printf '\n[%s/%s] %s\n' "$((index + 1))" "${#BATCH_ROOTS[@]}" "${root}"
  COLLECTOR_BOOTSTRAP_ROOTS="${root}" "${ROOT_DIR}/scripts/run_collector.sh" bootstrap
done
