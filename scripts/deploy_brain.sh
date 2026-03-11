#!/usr/bin/env bash
set -euo pipefail

SERVER_USER="${SERVER_USER:-deployer}"
SERVER_HOST="${SERVER_HOST:-104.131.63.231}"
SERVER_SSH_KEY="${SERVER_SSH_KEY:-$HOME/.ssh/id_ed25519}"
APP_DIR="${APP_DIR:-/opt/dusrabheja}"
GIT_REMOTE_URL="${GIT_REMOTE_URL:-https://github.com/AhmadSK95/duSraBheja.git}"
GIT_REF="${1:-main}"
LOCAL_ENV_FILE="${LOCAL_ENV_FILE:-.env}"

if [[ ! -f "$LOCAL_ENV_FILE" ]]; then
  echo "Missing env file: $LOCAL_ENV_FILE" >&2
  exit 1
fi

SSH=(ssh -i "$SERVER_SSH_KEY" "${SERVER_USER}@${SERVER_HOST}")

"${SSH[@]}" "sudo -n mkdir -p '$APP_DIR' && sudo -n chown '$SERVER_USER:$SERVER_USER' '$APP_DIR'"

"${SSH[@]}" "
  set -euo pipefail
  if [ ! -d '$APP_DIR/.git' ]; then
    git clone '$GIT_REMOTE_URL' '$APP_DIR'
  fi
  cd '$APP_DIR'
  git fetch origin
  git checkout '$GIT_REF'
  git pull --ff-only origin '$GIT_REF'
"

"${SSH[@]}" "cat > '$APP_DIR/.env'" < "$LOCAL_ENV_FILE"

"${SSH[@]}" "
  set -euo pipefail
  cd '$APP_DIR'
  sudo -n docker compose build --pull
  sudo -n docker compose run --rm brain-api alembic upgrade head
  sudo -n docker compose up -d
  sudo -n docker compose ps
"
