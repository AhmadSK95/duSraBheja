# Self Hosting

## Minimum stack

- Python 3.12+
- PostgreSQL 16 with `pgvector`
- Redis
- a Discord bot token
- at least one LLM provider for reasoning and one embedding-capable provider

## First-run setup

```bash
cp .env.example .env
cp providers.example.yaml providers.yaml
uv sync --extra dev
uv run alembic upgrade head
```

## Local services

```bash
uv run python -m src.api.app
uv run python -m src.bot.main
uv run python -m src.worker.main
uv run python -m src.mcp.server
```

## Public site

- point a public host such as `brain.example.com` at your server
- keep `/dashboard/*` behind login
- keep Turnstile enabled for public chat
- seed public facts from approved markdown or private canonical records, then run:

```bash
./.venv/bin/python scripts/refresh_public_surface.py
```

## Production

This repo includes deployment helpers for a DigitalOcean-style single-droplet setup, but the application itself is standard FastAPI + worker + Redis + PostgreSQL and can be hosted elsewhere.
