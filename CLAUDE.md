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
3. **Classify task** calls classifier agent (Llama 3.1 8B via NIM) → if confidence ≥ 0.75, enqueues `JOB_GENERATE_EMBEDDINGS`; if below, creates ReviewQueue + clarification question
4. **Embed task** chunks text (512 tokens, 64 overlap), embeds via NIM `nv-embedqa-e5-v5` (1024d) → enqueues `JOB_PROCESS_LIBRARIAN`
5. **Librarian task** calls librarian agent (Llama 3.3 70B via NIM) → merges into existing Note or creates new one

### Continuous Background Jobs

Only two crons remain (`src/worker/main.py:WorkerSettings.cron_jobs`):

- **Reminders** — fires due reminders (every minute)
- **Public Surface Refresh** — rebuilds the public-fact snapshot once a day at `public_surface_refresh_hour`:`public_surface_refresh_minute`

Cognition (synthesis across signals) is **not** on a cron. It triggers on-demand from `worker/tasks/librarian.py` after every `cognition_trigger_threshold` (default 20) successful merges, tracked via the `brain_counters` table.

Boards, digest, voice/persona refresh, knowledge refresh, and the product-improvement cycle were removed in the lean redesign.

## Key Layers

| Layer | Location | Role |
|-------|----------|------|
| **Agents** | `src/agents/` | Prompt functions wrapping NIM LLM calls. NOT separate processes. Only `classifier`, `librarian`, `retriever`, `clarifier` exist (plus `base.py`). |
| **Services** | `src/services/` | Business logic — `query`, `library`, `cognition`, `identity`, `indexing`, `planner`, `project_state`, `providers`, `public_surface`, `reminders`, `secrets`, `session_bootstrap`, `source_ingest`, `story`, `sync` |
| **Worker Tasks** | `src/worker/tasks/` | ARQ async jobs — `ingest`, `classify`, `embed`, `librarian`, `clarify`, `cognition`, `public_surface`, `reminders` |
| **Extractors** | `src/worker/extractors/` | File format handlers (router.py dispatches by MIME) |
| **API Routes** | `src/api/routes/` | brain.py (private API), dashboard.py (private UI), public.py (public site) |
| **MCP Tools** | `src/mcp/tools/` | search, ask, capture, context, protocol, story |
| **Bot Cogs** | `src/bot/cogs/` | inbox.py (capture), commands.py (slash commands), admin.py |
| **Collector** | `src/collector/` | Local scanning — project files, git, Apple Notes, Chrome, life exports |
| **Lib** | `src/lib/` | store.py (core data access, vector search), llm.py (NIM wrapper — `claude.py` is a legacy shim), embeddings.py, audit.py, crypto.py, auth.py, provenance.py |
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

All agents route through `src/agents/base.py` → `agent_call()`, which wraps the NIM LLM call (via `src/lib/llm.py`) and auto-logs to `AuditLog` (agent name, action, model, tokens, cost, duration, trace_id). Individual agents (`classifier.py`, `librarian.py`, `retriever.py`, `clarifier.py`) are just prompt functions calling `agent_call`.

### LLM Calls

`src/lib/llm.py` provides `call_llm()`, `call_llm_conversation()`, `call_llm_vision()`. All return a dict with `{text, model, input_tokens, output_tokens, cost_usd, duration_ms, trace_id}`. `cost_usd` is always `Decimal("0")` on NIM free-tier. Model selection uses `model_for_role()` from `src/services/providers.py`. `src/lib/claude.py` is a legacy shim that re-exports these under the old `call_claude*` names.

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

---

# ACTIVE BUILD — Vault buildout + Phase 2-4 roadmap

> **Status as of 2026-06-02**: Phase 1 steps 1.1–1.4 shipped. Owner has set up + tested the vault unlock flow. We're paused before 1.5.
>
> **Next session opens with**: a tiny UX commit (show-password toggle + Caps Lock indicator on `/dashboard/vault/setup` and `/dashboard/vault/unlock`), then step 1.5.

## Design decisions already locked in

These came out of multi-session discussion with the owner. Don't re-litigate; build on them.

### Vault crypto model

- **Hybrid envelope encryption.** X25519 keypair generated at setup. Public key in plaintext on droplet; private key encrypted at rest with a KEK derived from the owner's passphrase via Argon2id.
- **The passphrase lives only in the owner's head.** Never on disk, never in `.env`, never in a manager. Forgetting it = vault is permanently unrecoverable. The owner has a paper backup.
- **Ingest path needs no unlock.** Worker uses `vault_crypto.encrypt_for_vault(plaintext, vault_public_key)` from anywhere — ECIES (X25519 + HKDF-SHA256 + AES-256-GCM). Reveal needs unlock.
- **Unlocked vault state is process-local.** Process-local dict in `src/services/vault.py` keyed by `unlock_session_id`. Container restart drops all unlocks. 8-hour idle TTL.
- **Multi-worker, when we get there, is solved with sticky sessions at the reverse proxy** — NOT with Redis-backed shared state (which would require a wrapping key on the droplet, collapsing the threat model). This is documented in `src/services/vault.py` module docstring.
- **Reveal flow uses one-time links + OTP, never the secret value over Discord.** Kepobot DMs a single-use reveal URL + a 6-digit OTP. The actual secret only ever displays on the dashboard over TLS, after fresh-auth.

### Schema (committed in migration `015_vault_tables.py`)

Five tables, all `vault_`-prefixed. The older `secret_records` table (line ~676 in `src/models.py`) is the canonical-library identity tracker and **stays untouched** — different scope. Phase 1 may eventually migrate the older subsystem onto the vault.

| Table | Role |
|---|---|
| `vault_material` | Singleton row (UNIQUE(singleton)) with salt, kdf_params, public key, encrypted private key, nonce, version |
| `vault_unlock_sessions` | Per-device unlock metadata + 8h TTL. The unwrapped key is in process RAM; this is the audit/intent layer |
| `vault_secrets` | Owner-confirmed secrets. Envelope is JSONB (alg + ephemeral_pub + nonce + ciphertext + aad_b64) |
| `vault_secret_candidates` | Pre-classifier + retro-scan hits awaiting review. UNIQUE(source_type, source_id, suggested_label) stops re-flagging |
| `vault_reveal_audits` | Append-only log of every reveal attempt, successful or denied |

### UX decisions

- Setup flow: diceware suggestion (8 words from 200-word builtin list, ~61 bits — upgrade to EFF Large 7,776 words is a small follow-up). Owner can type their own (≥16 chars + ≥50 entropy bits enforced server-side). Required "I've stored this safely" checkbox.
- Unlock flow: passphrase entry with vault-key-ID fingerprint shown so the owner can confirm they're unlocking the same vault. 8h TTL, auto-locks on inactivity. Manual lock button on `/dashboard/vault/`.
- **Pending (open with next session)**: show-password toggle + Caps Lock indicator on setup + unlock pages.

## Phase 1 — Vault + secret hygiene

### Done

- **1.1** `src/lib/vault_crypto.py` + `tests/test_vault_crypto.py` (25 tests). Argon2id KEK, X25519/HKDF/AES-256-GCM envelope, `initialize_vault`, `unlock`, `encrypt_for_vault`, `decrypt_from_vault`, `change_passphrase`. Commit `4c057f3`.
- **1.2** Alembic `015_vault_tables.py` + 5 ORM models (`VaultMaterial`, `VaultUnlockSession`, `VaultSecret`, `VaultSecretCandidate`, `VaultRevealAudit`). Commit `795399a`.
- **1.3** Setup flow: `src/services/vault.py` service layer, `src/lib/diceware.py` generator + entropy estimator, `src/api/routes/vault.py` (3 routes), `dashboard_vault_setup.html` template, `vault_setup_required_middleware` in `src/api/app.py`, dashboard CSS extension. Commits `f815c22` + `76a403e` (integration test) + `a74865c` (CSS). 35 tests total at this point.
- **1.4** Unlock flow: service-layer unlock state (process-local dict + DB session row), 4 new routes (`/dashboard/vault/` smart index, GET/POST unlock, POST lock), `dashboard_vault_unlock.html`, fingerprint CSS, nav retarget. Commit `2258000`. 65 tests total.

### Next — step 1.5: vault list view + first reveal flow

**Goal**: dashboard surface to actually SEE secrets in the vault (redacted by default) and reveal one (without Discord OTP yet — that's 1.6).

Files:
- `src/services/vault.py` extension: `list_secrets`, `get_secret`, `create_secret`, `reveal_secret` (calls `vault_crypto.decrypt_from_vault` using the unlocked vault for the session, writes a `VaultRevealAudit` row).
- `src/api/routes/vault.py` extension: `GET /dashboard/vault/` updated to show the list when unlocked, `GET /dashboard/vault/<uuid>` per-secret detail page, `POST /dashboard/vault/<uuid>/reveal` (returns the secret value as JSON or rendered inline ephemerally), `POST /dashboard/vault/new` (owner-added secret form, for testing the round-trip).
- `dashboard_vault_list.html` + `dashboard_vault_secret.html` templates.
- CSS: `.vault-list`, `.vault-list__row`, `.vault-secret-reveal` (auto-hide on blur).
- Tests: service tests for list/get/create/reveal; integration test for the unlock → list → reveal round-trip.

Auth check: every reveal must verify `is_session_unlocked(sid)` before calling `decrypt_from_vault`. Writes `VaultRevealAudit(outcome="success" | "denied_locked")`.

### Queued after 1.5

- **1.6** Kepobot reveal links. Discord bot DM commands (`/secret <label>`), single-use reveal URL generation with 60s TTL, OTP code returned in DM. Reveal URL hits a new `/dashboard/vault/reveal/<token>` route that validates the OTP + session and shows the secret ephemerally. Audit log gets `request_source="discord_kepobot"`.
- **1.7** Audit log dashboard. `/dashboard/vault/audit` showing recent `VaultRevealAudit` rows with anomaly hints (e.g., "3 reveals in 5 minutes from a new IP").
- **1.8** Pre-classifier scrub. Extend `src/services/secrets.py`'s detection patterns (regex + entropy + known prefixes: `ghp_`, `AKIA`, `sk-`, `eyJ` JWT-shaped, PEM `-----BEGIN`). Hook into `src/worker/tasks/classify.py` BEFORE the LLM sees the body. Split detected content: secret → encrypted with public key → `VaultSecretCandidate` row with status="pending"; rest of artifact proceeds with `[REDACTED]` markers. **This is the Phase 2 unblocker.**
- **1.9** Retro-scan ARQ task. Walks every `Note.body` + `EvidenceRecord.body` once, generates `VaultSecretCandidate` rows. High-confidence patterns (known prefixes) auto-status="pending" but display flagged; entropy-only hits go to a separate review queue.
- **1.10** Retro-scan review queue. `/dashboard/vault/candidates` page. Each candidate shows a redacted preview, source artifact link, "confirm + encrypt" or "dismiss false positive" buttons. Confirmed candidates get promoted to `VaultSecret`; the source artifact body gets redacted to `[REDACTED — see vault]`.
- **1.11** Retriever hard-filter. Add `WHERE facet != 'secret'` to chat-context queries in `src/services/public_surface.py` and any private retrieval path. Audit-log polish. Final tests + Phase 1 closeout.

## Phase 2 — Capture from anywhere

**Unblocked after step 1.8 lands** (so MCP captures get the same secret protection as Discord ones).

### Goals

- Owner can drop ideas into the brain from wherever they're working — Claude Code, dashboard, mobile share sheet — without opening Discord.
- The same async classification + embed + librarian pipeline that handles Discord captures runs on these inputs.
- Cross-session memory: Claude Code / Codex sessions write structured `Decision` and `OpenLoop` records that future sessions can pick up cleanly.

### Components

- **MCP `remember(text, category?, project?)` tool polish.** Already exists at `src/mcp/tools/capture.py`; needs an "active project context" pointer so captures auto-link to the project the session declared on bootstrap.
- **`ActiveProjectContext` table** that bootstrap/closeout writes. Lets the capture tool default project_slug to the active session's project without per-capture re-specification.
- **Dashboard `/capture` page**: textarea + optional category override + project pin + file attach. Same ingest pipeline downstream.
- **iOS Shortcut + iOS share-sheet payload.** JSON POST to `/api/private/capture`. 30 min of setup, lifetime utility.
- **Structured `Decision` records.** New `BrainDecision` table or use existing `SynthesisRecord` with `subtype="decision"`. Captured via MCP `remember_decision(decision, rationale, tradeoff?, project?)`. Future sessions surface these in the bootstrap brief.
- **Structured `OpenLoop` records.** Track "things we said we'd come back to." Cleared explicitly when resolved. Surface count in bootstrap.

## Phase 3 — Dashboard rewrite

**Unblocked after step 1.5 lands** (vault list view needs to exist to integrate into the new dashboard).

### Goals

Replace the transactional 5-page Atlas (What's New, Inbox, Library, Projects, Public Facts) with a navigation model that matches how knowledge actually gets used.

### Components

- **Unified `/dashboard/browse`** — semantic search box + filter sidebar (category, source, project, date, confidence, has-secrets). Replaces Inbox + Library.
- **`/dashboard/threads`** — topic-cluster view backed by `ThreadRecord` (already in the data model, currently not exposed).
- **`/dashboard/project/<slug>`** — per-project page: latest `ProjectStateSnapshot`, recent activity, open questions, recent decisions, linked secrets (from vault).
- **`/dashboard/brain`** — logged-in chat, full-fidelity (no public-side scrubbing). Same MCP-style tools available.
- **`/dashboard/chat-history`** — past brain conversations, searchable.
- **Existing 5 pages**: redirect or absorb. What's New becomes a notification surface on the new home; Public Facts moves under a Vault-adjacent area since it's the same approval-queue pattern.
- **Design tokens**: the public site is using clean tokens (waterfront / dark / amber). Apply consistent tokens to the new dashboard; the current Atlas CSS has overlap.

## Phase 4 — Calendar (read-only v1)

**Independent of Phases 1-3.** Can land whenever.

### Components

- **Google OAuth flow** on the dashboard. Settings: which calendar(s) to sync. Initial scope read-only (`calendar.readonly`).
- **ARQ task `sync_calendar_events`** — periodic sync (every 15 min) of upcoming events. Stored as `EvidenceRecord` with `source_kind="google_calendar"` + a new `CalendarEvent` table for queryable fields (start_time, end_time, attendees, title, location).
- **MCP tools**: `calendar_upcoming(days=7)`, `calendar_find_free_slot(duration, before)`. Owner-only.
- **`/dashboard/week`** — time-by-project view. "Where my week is going." Pulls from CalendarEvent + ProjectStateSnapshot.
- **Planner agent** (small, optional): given current project state + calendar + recent captures, answer "what should I focus on this week?" Conversational on the logged-in `/brain`.

Write capability (add events from natural language) is **deliberately out of scope for v1**. Write surface = next pass after we see how reads are used.

## Open architectural questions (to revisit, not blocking)

- **EFF Large diceware bundling.** Current generator uses a 200-word builtin (~61 bits at 8 words). EFF Large gets to ~103 bits. Small follow-up commit; documented in `src/lib/diceware.py` docstring.
- **Multi-worker uvicorn.** Single worker today is fine. When we go to 2: add reverse-proxy sticky session config (`hash $cookie_brain_dashboard_session`). No vault code changes.
- **Cloudflare edge caching for public site.** Mentioned in early audits. Public pages are sub-100ms server-side; CF caching would drop to ~10ms global. Cheap win.
- **The 3 oversized files.** `src/api/routes/public.py` (~2300 lines), `src/lib/store.py` (~3000 lines), `src/services/public_surface.py` (~3000 lines). Split when convenient; not blocking anything.

## How to pick this up in a new session

1. Read this section. The decisions section is the most important — don't re-derive design calls.
2. `git log --oneline | head -30` to see commit history (look for `vault` and `Phase 1` commits).
3. Run the vault test suite: `./.venv/bin/python -m pytest tests/test_vault_*.py -q` (should be 65/65).
4. Owner has tested the unlock flow on prod; ask them to confirm before resuming if more than a day has passed.
5. **First action**: ship the show-password + Caps Lock indicator UX commit (apply to both `dashboard_vault_setup.html` and `dashboard_vault_unlock.html` + a small CSS block).
6. **Then**: step 1.5 (vault list view + first reveal flow).
