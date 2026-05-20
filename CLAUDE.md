# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

duSraBheja — Ahmad's personal "open brain." Discord `#inbox` captures text/images/links, the worker pipeline classifies + stores + canonicalizes them, and "ask my brain" answers via RAG. A lean public site (about, projects, contact, chatbox) exposes only owner-approved facts. Runs entirely on NVIDIA NIM free-tier models.

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

# Embeddings — reindex chunks after switching the NIM embedding model
uv run python scripts/reindex_embeddings.py                  # full reindex
uv run python scripts/reindex_embeddings.py --batch 64        # tune batch
uv run python scripts/reindex_embeddings.py --dry-run         # count only

# Docker (production)
docker compose up -d                     # Start all services
docker compose logs -f brain-bot         # Follow logs
```

## Architecture

```
Discord #inbox → Bot (cogs/inbox.py) → ARQ Job Queue (Redis)
                                             ↓
                                        Worker Process
                                        ├── Extract (PDF/image/excel/docx/link)
                                        ├── Classify (Llama 3.1 8B → structured JSON)
                                        ├── Embed (NIM nv-embedqa-e5-v5, 1024d)
                                        ├── Librarian merge (Llama 3.3 70B → canonical Note)
                                        └── Cognition (every N=20 merges, on-demand)

FastAPI (src/api/) → 5-page Atlas dashboard + lean public site + REST API
MCP Server (FastMCP) → Brain tools for Claude Code / Codex
Collector (src/collector/) → Local file/browser/agent history ingestion
```

### Pipeline Flow

1. **Bot receives message** → enqueues `JOB_PROCESS_INBOX_MESSAGE`
2. **Ingest task** downloads attachments, extracts text via router → enqueues `JOB_CLASSIFY_ARTIFACT`
3. **Classify task** calls classifier agent (Haiku) → if confidence ≥ 0.75, enqueues `JOB_GENERATE_EMBEDDINGS`; if below, creates ReviewQueue + clarification question
4. **Embed task** chunks text (512 tokens, 64 overlap), embeds via OpenAI → enqueues `JOB_PROCESS_LIBRARIAN`
5. **Librarian task** calls librarian agent (Sonnet) → merges into existing Note or creates new one

### Continuous Background Jobs

Only two crons remain (`src/worker/main.py:WorkerSettings.cron_jobs`):

- **Reminders** — fires due reminders (every minute)
- **Public Surface Refresh** — rebuilds the public-fact snapshot once a day at `public_surface_refresh_hour`:`public_surface_refresh_minute`

Cognition (synthesis across signals) is **not** on a cron. It triggers on-demand from `worker/tasks/librarian.py` after every `cognition_trigger_threshold` (default 20) successful merges, tracked via the `brain_counters` table.

Boards, digest, voice/persona refresh, knowledge refresh, and the product-improvement cycle were removed in the lean redesign.

## Key Layers

| Layer | Location | Role |
|-------|----------|------|
| **Agents** | `src/agents/` | Prompt functions wrapping Claude API calls. NOT separate processes. |
| **Services** | `src/services/` | Business logic (query, digest, boards, knowledge, secrets, etc.) |
| **Worker Tasks** | `src/worker/tasks/` | ARQ async jobs — bot enqueues, worker processes |
| **Extractors** | `src/worker/extractors/` | File format handlers (router.py dispatches by MIME) |
| **API Routes** | `src/api/routes/` | brain.py (private API), dashboard.py (private UI), public.py (public site) |
| **MCP Tools** | `src/mcp/tools/` | search, ask, capture, context, protocol, story, website |
| **Bot Cogs** | `src/bot/cogs/` | inbox.py (capture), commands.py (slash commands), admin.py |
| **Collector** | `src/collector/` | Local scanning — project files, git, Apple Notes, Chrome, life exports |
| **Lib** | `src/lib/` | store.py (core data access, vector search), claude.py (LLM wrapper), audit.py, crypto.py, embeddings.py |
| **Core modules** | `src/` (top level) | `models.py` (all SQLAlchemy ORM models), `database.py` (async engine + session factory), `config.py` (Pydantic Settings), `constants.py` (canonical categories, sources, query modes) |

## Code Patterns

### Database Sessions

All data access goes through `src/lib/store.py` (3000+ lines). Every function takes `session: AsyncSession` as its first parameter. Sessions are obtained via context manager from `src/database.py`:

```python
async with async_session() as session:
    result = await store.get_note(session, note_id)
    await session.commit()  # explicit commit required for mutations
```

**Naming conventions in store.py:** `get_*` (single record → `Model | None`), `list_*` (bulk queries), `create_*` (inserts), `update_*` (patches). All create/update functions set `created_at`/`updated_at` automatically.

### Agent Base Layer

All agents route through `src/agents/base.py` → `agent_call()`, which wraps the Claude SDK call and auto-logs to `AuditLog` (agent name, action, model, tokens, cost, duration, trace_id). Individual agents (`classifier.py`, `librarian.py`, `retriever.py`, `clarifier.py`, `storyteller.py`, `website_builder.py`) are just prompt functions calling `agent_call`.

### LLM Calls

`src/lib/claude.py` provides three functions: `call_claude()`, `call_claude_conversation()`, `call_claude_vision()`. All return a dict with `{text, model, input_tokens, output_tokens, cost_usd, duration_ms, trace_id}`. Model selection uses `model_for_role()` from providers config.

### Worker Tasks

ARQ async jobs — each task is `async def task_name(ctx, ...)`. Tasks obtain their own sessions and use `log = logging.getLogger("brain-worker.task_name")`. Worker runs max 5 concurrent jobs with 5-minute timeout. Cron jobs are registered in `WorkerSettings.cron_jobs`.

### MCP Tool Registration

```python
def register(mcp: FastMCP):
    @mcp.tool()
    async def tool_name(arg: str) -> dict:
        """Docstring becomes tool description."""
        async with async_session() as session:
            return await service_call(session, ...)
```

### API Authentication

- **Dashboard:** session-based auth (SessionMiddleware with secure cookies) + Bearer token fallback. Five pages — `/dashboard/` (What's New since `DashboardViewState.last_seen_at`), `/dashboard/inbox`, `/dashboard/library`, `/dashboard/projects`, `/dashboard/public-facts` (approval queue). No on-render LLM calls; every page is one or two indexed SQL queries.
- **Private API (`/api/*`):** Bearer token via `Authorization: Bearer {api_token}`
- **Public routes (`/`, `/about`, `/projects`, `/projects/{slug}`, `/contact`):** no auth, read from `PublicFactRecord` allowlist (only `approved=True` rows) and `PublicProjectSnapshot`. Architecture, repo links, file paths, hosting details are scrubbed before save.
- **Public chatbot (`/api/public/chat`):** rate-limited per IP, optional Cloudflare Turnstile, `_hard_reject` blocks architecture/infra/secret probes via `PUBLIC_REJECT_HINTS`, output scrubbed for GitHub URLs / IPs / SSNs.

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

- **Public** pages (`/`, `/about`, `/projects`, `/projects/{slug}`, `/contact`, `/api/public/chat`) read from `PublicFactRecord` (`approved=True` only) and `PublicProjectSnapshot`.
- **Private** (`/dashboard/*`, `/api/*`, MCP/CLI) accesses the full brain.
- **Secrets** encrypted in vault with Discord OTP verification (`ProtectedContent`, `PermissionGrant`).

## LLM Model Routing

All chat, vision, and embedding calls go to NVIDIA NIM (OpenAI-compatible) via `src/lib/llm.py` and `src/lib/embeddings.py`. The single async client lives in `nim_client()` / `_client()` and points at `settings.nvidia_base_url` (default `https://integrate.api.nvidia.com/v1`).

Per-role defaults (override in `providers.yaml`):

| Role | Model | Why |
|------|-------|-----|
| Classifier | `meta/llama-3.1-8b-instruct` | Fast, cheap, structured JSON |
| Vision (image OCR) | `meta/llama-3.2-11b-vision-instruct` | Cheapest vision on NIM free tier |
| Librarian (merge) | `meta/llama-3.3-70b-instruct` | Strong general reasoning |
| RAG / public chat | `meta/llama-3.3-70b-instruct` | One model kept warm for query path |
| Reasoning heavy (rare) | `nvidia/llama-3.1-nemotron-70b-instruct` | Slightly better reasoning |
| Embedding | `nvidia/nv-embedqa-e5-v5` (1024d) | Retrieval-tuned, on free tier |

NIM free-tier has no per-token cost, so `cost_usd` is always `Decimal("0")` in AuditLog. Token counts and model names are still logged for quota visibility.

Anthropic + OpenAI integrations were removed in the lean redesign. Audio transcription (Whisper) was dropped — voice notes captured in Discord are stored but not transcribed.

## Categories

task, project, people, idea, note, resource, reminder, daily_planner, weekly_planner

## Key Rules

- **Async everywhere.** Use `async`/`await` throughout. SQLAlchemy AsyncSession, httpx.AsyncClient, ARQ async jobs.
- **Agents are prompt functions.** Each agent is a module wrapping a NIM chat call via `src/lib/llm.py`. Not separate processes.
- **Bot enqueues, worker processes.** The Discord bot never blocks on LLM calls or file parsing.
- **Separate DB.** Use `brain_db` with `brain_user`. Never touch barbershop database.
- **Structured classification.** Classifier returns strict JSON: `{category, confidence, capture_intent, entities[], tags[], priority, suggested_action, summary}`.
- **Confidence threshold = 0.75.** Below → needs-review flow with max 2 clarification attempts.
- **All config via pydantic-settings** (`src/config.py`). Secrets in `.env`, never in code. NVIDIA NIM key in `NVIDIA_API_KEY`.
- **Public surface = approved facts only.** Daily refresh stages new `PublicFactRecord` rows with `approved=False`; owner approves via `/dashboard/public-facts`. The chatbot retrieves only `approved=True` rows.
- **No architecture leaks publicly.** Tech stack chips, repo URLs, file paths, hostnames, IPs, deploy details — none of these reach `/projects`, `/about`, or chatbot output. Hardened in `_scrub_public_output`, `PUBLIC_REJECT_HINTS`, and `CLONE_SYSTEM_PROMPT_TEMPLATE`.
- **Audit logging.** Every LLM call logs to audit_log with model + tokens. NIM is free-tier so `cost_usd` is always 0.

## Config

Central config: `src/config.py` (Pydantic Settings). Key env vars in `.env.example`.

Model routing can be customized via `providers.yaml` (see `providers.example.yaml`) — supports anthropic, openai, and local providers.

## Testing

- Framework: pytest + pytest-asyncio (async mode: auto)
- Test directory: `tests/`
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
