#!/usr/bin/env bash
set -euo pipefail

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example"
fi

if [[ ! -f providers.yaml ]]; then
  cp providers.example.yaml providers.yaml
  echo "Created providers.yaml from providers.example.yaml"
fi

uv sync --extra dev
echo "Local bootstrap complete."
