# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

duSraBheja — a Discord-based Brain OS. Captures text/images/audio/links from Discord `#inbox`, promotes raw evidence into canonical memory (evidence → observations → episodes → threads → entities → syntheses), exposes everything to agents via MCP/HTTP/CLI, and serves a public profile site from an approved-facts allowlist.

## Commands

```bash
# Install
uv sync --extra dev

# Run services locally (each in its own terminal)
uv run python -m src.bot.main            # Discord bot
uv run python -m src.worker.main         # ARQ background worker
uv run python -m src.mcp.server          # MCP server
uv run python -m src.api.main            # FastAPI server

# Database
uv run alembic upgrade head              # Run migrations
uv run alembic revision --autogenerate -m "description"  # New migration

# Lint
uv run ruff check src/ tests/            # Lint check
uv run ruff check --fix src/ tests/      # Lint fix
uv run ruff format src/ tests/           # Format

# Test
uv run pytest                            # All tests
uv run pytest tests/test_classifier.py   # Single file
uv run pytest -k "test_name"             # Single test by name
uv run pytest -x                         # Stop on first failure

# Docker (production)
docker compose up -d                     # Start all services
docker compose logs -f brain-bot         # Follow logs
```

## Architecture

```
Discord #inbox → Bot (cogs/inbox.py) → ARQ Job Queue (Redis)
                                             ↓
                                        Worker Process
                                        ├── Extract (PDF/image/audio/excel/link)
                                        ├── Classify (Haiku 4.5 → structured JSON)
                                        ├── Embed (OpenAI text-embedding-3-small, 1536d)
                                        ├── Librarian merge (Sonnet 4.6 → canonical Note)
                                        └── Route to Discord channel

FastAPI Server (src/api/) → Private dashboard + public profile site + REST API
MCP Server (FastMCP)      → Exposes brain tools to Claude Code / Codex
Collector (src/collector/) → Local file/browser/agent history ingestion
```

### Pipeline Flow

1. **Bot receives message** → enqueues `JOB_PROCESS_INBOX_MESSAGE`
2. **Ingest task** downloads attachments, extracts text via router → enqueues `JOB_CLASSIFY_ARTIFACT`
3. **Classify task** calls classifier agent (Haiku) → if confidence ≥ 0.75, enqueues `JOB_GENERATE_EMBEDDINGS`; if below, creates ReviewQueue + clarification question
4. **Embed task** chunks text (512 tokens, 64 overlap), embeds via OpenAI → enqueues `JOB_PROCESS_LIBRARIAN`
5. **Librarian task** calls librarian agent (Sonnet) → merges into existing Note or creates new one

### Continuous Background Jobs

- **Daily/Weekly Boards** — narrative summaries posted to Discord
- **Daily Digest** — morning operating brief (cron at hour 8)
- **Knowledge Refresh** — project state recomputation (every 6h)
- **Cognition** — synthesis of observations (every 4h)
- **Voice Refresh** — persona packet update (every 5h)
- **Reminders** — fire due reminders

## Key Layers

| Layer | Location | Role |
|-------|----------|------|
| **Agents** | `src/agents/` | Prompt functions wrapping Claude API calls. NOT separate processes. |
| **Services** | `src/services/` | Business logic (query, digest, boards, knowledge, secrets, etc.) |
| **Worker Tasks** | `src/worker/tasks/` | ARQ async jobs — bot enqueues, worker processes |
| **Extractors** | `src/worker/extractors/` | File format handlers (router.py dispatches by MIME) |
| **API Routes** | `src/api/routes/` | brain.py (private API), dashboard.py (private UI), public.py (public site) |
| **MCP Tools** | `src/mcp/tools/` | search, ask, capture, context, protocol, story |
| **Bot Cogs** | `src/bot/cogs/` | inbox.py (capture), commands.py (slash commands), admin.py |
| **Collector** | `src/collector/` | Local scanning — project files, git, Apple Notes, Chrome, life exports |
| **Lib** | `src/lib/` | store.py (core data access, vector search), claude.py (LLM wrapper), audit.py, crypto.py, embeddings.py |

## Memory Model (Canonical Library)

Story is presentation, not storage. Raw artifacts are promoted into:

1. **EvidenceRecord** — raw factual data with provenance
2. **ObservationRecord** — interpreted facts (certainty-rated)
3. **EpisodeRecord** — time-bounded contexts (sessions, meetings, sprints)
4. **ThreadRecord** — topic conversations with aliases
5. **EntityRecord** — named things (people, projects, topics)
6. **SynthesisRecord** — derived insights (patterns, recommendations)

All linked by provenance IDs (evidence_ids, thread_ids, entity_ids) for traceability. Plus **JournalEntry** for grounded story events and **ProjectStateSnapshot** for durable project status.

## Public / Private Split

- **Public** pages (`/`, `/about`, `/projects`, `/open-brain`) read from `PublicFactRecord` allowlist only
- **Private** (`/dashboard/*`, `/api/*`, MCP/CLI) accesses the full brain
- **Secrets** encrypted in vault with Discord OTP verification (`ProtectedContent`, `PermissionGrant`)

## LLM Model Routing

| Task | Model | Why |
|------|-------|-----|
| Classification | `claude-haiku-4-5-20251001` | Fast, cheap, structured JSON |
| Clarification | `claude-sonnet-4-6` | Nuanced question generation |
| Librarian (merge) | `claude-sonnet-4-6` | Intelligent info merging |
| Storyteller (boards, digest, project state) | `claude-sonnet-4-6` | Narrative generation |
| RAG synthesis | `claude-sonnet-4-6` / `claude-opus-4-6` | Best reasoning |
| Image OCR | `claude-haiku-4-5-20251001` (vision) | Cheap, no Tesseract |

Every LLM call must log model, tokens, cost to `AuditLog` via `src/lib/audit.py`.

## Categories

task, project, people, idea, note, resource, reminder, daily_planner, weekly_planner

## Key Rules

- **Async everywhere.** Use `async`/`await` throughout. Never mix sync and async in a call path. SQLAlchemy AsyncSession, httpx.AsyncClient, ARQ async jobs.
- **Agents are prompt functions.** Each agent is a module with a function wrapping a Claude API call. Not separate processes.
- **Bot enqueues, worker processes.** The Discord bot never blocks on LLM calls or file parsing.
- **Separate DB.** Use `brain_db` with `brain_user`. Never touch barbershop database.
- **Structured classification.** Classifier returns strict JSON: `{category, confidence, capture_intent, entities[], tags[], priority, suggested_action, summary}`.
- **Confidence threshold = 0.75.** Below → needs-review flow with max 2 clarification attempts.
- **All config via pydantic-settings** (`src/config.py`). Secrets in `.env`, never in code.
- **Cost tracking.** Every LLM call logs to audit_log with model, tokens, cost_usd.

## Config

Central config: `src/config.py` (Pydantic Settings). Key env vars in `.env.example`.

Model routing can be customized via `providers.yaml` (see `providers.example.yaml`) — supports anthropic, openai, and local providers.

## Testing

- Framework: pytest + pytest-asyncio (async mode: auto)
- Test directory: `tests/` (39 test files)
- Ruff: line-length 100, target py312, rules E/F/I/N/W

## Agent Session Loop

Every Claude Code / Codex session should bootstrap from the brain and publish a closeout.

```bash
# Start
./.venv/bin/python scripts/brain_session.py bootstrap \
  --agent-kind claude --project-hint duSraBheja

# End
./.venv/bin/python scripts/brain_session.py closeout \
  --agent-kind claude --session-id <id> \
  --project-ref duSraBheja --summary "<what changed>"
```

If MCP is connected, prefer tools: `bootstrap_session`, `publish_progress`, `publish_session_closeout`, `resolve_project_identity`, `query_brain_mode`.

## Deployment

- **Droplet**: 104.131.63.231
- **Services**: brain-redis, brain-bot, brain-worker, brain-mcp, brain-api
- **Ports**: Redis 6399 (internal), MCP 8100 (localhost), API 8000 (localhost)
- **Barbershop safety**: Separate DB, separate Docker network, connection pool limits
