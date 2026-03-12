# duSraBheja v2 — Discord Brain

## What This Is

A Discord-based personal AI command center / second brain for Ahmad. Drop anything into `#inbox`, the brain classifies it, stores it, makes it searchable via RAG, and exposes it to Claude Code / Codex via MCP.

## Tech Stack

- **Language**: Python 3.12+
- **Package manager**: uv
- **Discord**: discord.py 2.x (Cogs pattern)
- **Web framework**: FastAPI (health endpoints, future API)
- **Job queue**: ARQ (async, Redis-backed)
- **Database**: PostgreSQL 16 + pgvector (async via SQLAlchemy 2.0 + asyncpg)
- **Migrations**: Alembic
- **LLM**: Anthropic Claude (Haiku 4.5, Sonnet 4.6, Opus 4.6)
- **Embeddings**: OpenAI text-embedding-3-small (1536d)
- **MCP**: FastMCP (Python SDK)
- **Deployment**: Docker Compose on DigitalOcean droplet

## Architecture

```
Discord #inbox → Bot (discord.py) → ARQ Job Queue (Redis)
                                         ↓
                                    Worker Process
                                    ├── Extract text (PDF/image/audio/excel)
                                    ├── Classify (Claude Haiku 4.5)
                                    ├── Embed (OpenAI text-embedding-3-small)
                                    ├── Merge notes (Claude Sonnet 4.6 Librarian)
                                    └── Route to Discord channel

MCP Server (FastMCP) → Exposes brain tools to Claude Code / Codex
```

## LLM Model Routing

| Task | Model ID | Why |
|---|---|---|
| Classification | `claude-haiku-4-5-20251001` | Fast, cheap, structured JSON output |
| Clarification | `claude-sonnet-4-6` | Nuanced question generation |
| Librarian (merge) | `claude-sonnet-4-6` | Intelligent info merging |
| RAG synthesis | `claude-sonnet-4-6` / `claude-opus-4-6` | Best reasoning |
| Image OCR | `claude-haiku-4-5-20251001` (vision) | Cheap, no Tesseract |

## Categories (7)

task, project, people, idea, note, reminder, planner

## Key Rules

- **Async everywhere**: Use `async`/`await` throughout. Never mix sync and async in a call path.
- **Agents are prompt functions**: Each agent is a Python module with a function that wraps a Claude API call. NOT separate processes.
- **Bot enqueues, worker processes**: The Discord bot never blocks on LLM calls or file parsing. It enqueues ARQ jobs.
- **Separate DB**: Use `brain_db` database with `brain_user` role. Never touch the barbershop database.
- **Cost tracking**: Every LLM call must log model, tokens, cost to audit_log.
- **No secrets in code**: All secrets via `.env` + pydantic-settings. Never commit `.env`.
- **Structured classification**: Classifier always returns strict JSON: `{category, confidence, entities[], tags[], priority, suggested_action, summary}`
- **Confidence threshold**: 0.75. Below = needs-review flow.

## Project Structure

```
src/
├── config.py           # Pydantic Settings (central config)
├── models.py           # SQLAlchemy ORM models
├── database.py         # Async engine + session factory
├── bot/                # Discord bot (Cogs pattern)
│   ├── main.py
│   └── cogs/
├── worker/             # ARQ background worker
│   ├── main.py
│   ├── tasks/          # Job definitions
│   └── extractors/     # File format handlers
├── agents/             # AI agent prompt functions
├── mcp/                # MCP server + tools
└── lib/                # Shared utilities
```

## Commands

```bash
# Dev
uv run python -m src.bot.main          # Run Discord bot
uv run python -m src.worker.main       # Run ARQ worker
uv run python -m src.mcp.server        # Run MCP server
uv run alembic upgrade head            # Run migrations

# Docker
docker compose up -d                   # Start all services
docker compose logs -f brain-bot       # Follow bot logs
```

## Agent Session Loop

Every Claude Code or Codex session should start by rebooting from the brain and end by publishing a closeout.

Start:

```bash
./.venv/bin/python scripts/brain_session.py bootstrap \
  --agent-kind claude \
  --project-hint duSraBheja
```

Close out:

```bash
./.venv/bin/python scripts/brain_session.py closeout \
  --agent-kind claude \
  --session-id <session-id> \
  --project-ref duSraBheja \
  --summary "<what changed>"
```

If MCP is connected, prefer the shared tools:

- `bootstrap_session`
- `publish_progress`
- `publish_session_closeout`
- `resolve_project_identity`
- `query_brain_mode`

## Deployment

- **Droplet**: 104.131.63.231
- **Services**: brain-redis, brain-bot, brain-worker, brain-mcp
- **Ports**: Redis 6399 (internal), MCP 8100 (localhost only)
- **Barbershop safety**: Separate DB, separate Docker network, connection pool limits
