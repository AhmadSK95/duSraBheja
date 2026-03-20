# duSraBheja Project History: A Complete Chronological Journey

**Project**: duSraBheja ("second brain" in Hindi/Urdu) — an open-source Brain OS for Discord intake, private memory, agent bootstrapping, and public profile surfaces

**Duration**: Feb 24, 2026 → Mar 18, 2026 (23 days, 92 commits)
**Author**: Ahmad (Moenuddeen Ahmad Shaik)
**Status**: Production deployment with autonomous website builder and digital clone chatbot

---

## Executive Summary

duSraBheja is a sophisticated personal AI system that evolved through multiple architectural pivots:

1. **Phase 0-1 (Feb 24)**: TypeScript/WhatsApp foundation with PostgreSQL, NATS, Temporal, and Ollama
2. **Phase 2-3 (Feb 24)**: Multi-agent LangGraph swarm (Planner→Critic→Sentinel→Executor) with approval gates and storyboarding
3. **v2 Rewrite (Mar 5)**: Complete Python/Discord migration with ARQ workers and MCP integration
4. **Story-First Era (Mar 11-18)**: Board-driven narrative memory, Atlas dashboard, canonical library, public profiles, and autonomous website generation

The system captures evidence from Discord, classifies it via Claude Haiku, enriches with semantic embeddings, promotes to canonical memory (evidence→observations→episodes→threads→entities→syntheses), and exposes everything to agents via MCP, REST API, and CLI. A public face serves approved facts only. The final evolution adds autonomous website management and a conversational digital clone for public interaction.

---

## Detailed Chronological Timeline

### Phase 0-1: TypeScript Foundation (Feb 24, 15:37-16:32)

**Commit 1: Phase 0 + Phase 1: Foundation and Core Loop**
*Feb 24, 15:37:03 -0500 | 4ef963c*

Initial launch with comprehensive infrastructure:

**Phase 0 — Infrastructure (7,975 insertions across 28 files)**
- PostgreSQL 16 with pgvector (768-dimension semantic vectors) for brain node embeddings
- NATS 2.12.4 with JetStream for event streaming and pub/sub patterns
- Ollama local AI (llama3.1:8b + nomic-embed-text) for embedding generation
- Temporal.io via Docker Compose for durable workflow orchestration
- 11-table database schema: artifacts, classifications, nodes, agents, sessions, etc.
- Seed agent policies and rules (embedded in SQL)
- Health check suite: 14/14 passing infrastructure tests

**Phase 1 — Core Inbox Loop**
- WhatsApp gateway (whatsapp-web.js) with 8 initial commands
- NATS consumer ingestion: WhatsApp messages → NATS topic → worker queue
- Ollama-based text classification (0.7 confidence threshold as gate)
- Review queue for sub-threshold items with confidence feedback
- PostgreSQL artifact store with 768-dim semantic embeddings
- WhatsApp responder with structured JSON confirmations
- Audit trail on every automated action (decision log)
- Daily summary Temporal workflow (scheduled 8am daily)

**Architecture Highlights**:
- Event-driven: whatsapp.ts gateway publishes to NATS, inbox-processor.ts subscribes
- Separation of concerns: gateway, processor, responder as distinct workers
- Confidence-based gating: low-confidence artifacts routed to human review
- Comprehensive documentation: Master Architecture Spec (709 lines), PRD (31 functional requirements), source-of-truth prompt notes

This was the starting point: a cohesive TypeScript system with proper infrastructure and clear separation of concerns. Focus on automation with safety gates (confidence threshold + review queue).

---

**Commit 2: Self-chat filter, CONNECTION_DRAINING fix, background service**
*Feb 24, 15:56:17 -0500 | 5bf2f10*

Early production stabilization (82 insertions, 4 files):

- **Self-chat filter**: WhatsApp gateway now filters to only process messages from self-chat (myChatId filter on client ready event)
- **CONNECTION_DRAINING resilience**: Inbox processor now handles NATS CONNECTION_DRAINING gracefully with exponential backoff instead of infinite error loop
- **Background service script**: Added scripts/start.sh for launchd background service startup
- **Testing helper**: Added scripts/send-test.ts for manual NATS message injection during development

Critical operations fix: The CONNECTION_DRAINING handling prevents worker crashes when NATS shuts down gracefully.

---

**Commit 3: Single-process architecture, Chromium optimization, compiled JS**
*Feb 24, 16:32:48 -0500 | 2ef1194*

Major optimization refactor (82 insertions, 7 files):

**Architecture Simplification**:
- Merged gateway, inbox processor, and responder into **single Node process** (was 10 separate processes using child_process + tsx)
- Eliminated tsx runtime overhead by building to plain JS via tsc, running with node directly
- Node heap capped at 256MB via --max-old-space-size=256 flag

**Performance Tuning**:
- Added Chromium memory-saving flags: disable-gpu, single-process, disable-extensions
- Compiled dist/index.js as primary artifact instead of running ts-node
- launchd plist now directly executes compiled binary

**Bug Fixes**:
- Fixed sendBotMessage recursive call bug that caused stack overflow on command execution
- Switched from message to message_create event for proper self-chat support

**Rationale**: Moving from multi-process to single-process reduced resource contention, simplified deployment, and eliminated serialization overhead. Direct JS compilation reduced latency.

---

**Commit 4: Phase 2 + Phase 3: Multi-agent swarm, safety gates, storyboards, voice & PDF**
*Feb 24, 19:00:36 -0500 | 3d118a2*

Massive feature expansion (4,337 insertions, 30 files) — adds agent orchestration, project tracking, GitHub integration, approval gates, and generative storyboarding:

**Phase 2 — Project Tracker + GitHub Integration**:
- Project/task CRUD operations with brain node linking (entities can point to projects)
- GitHub repo polling every 15 minutes
- Stale item nudges every 6 hours (reminders for untouched tasks)
- 14 new WhatsApp commands: proj, task, gh, stale, etc.
- project-store.ts: 344 lines managing project state and task branching

**Phase 3 — Multi-Agent LangGraph Pipeline** (Major new capability):
- **Agent orchestration**: Planner (Claude Sonnet) → Critic (Gemini 2.5 Pro) → Sentinel → Executor
- **FAIL-CLOSED safety policy engine**: R0-R4 risk tiers with escalating approval requirements
- **Approval gates**: R2+ actions require human approval in WhatsApp
- **Kill switch + lockdown**: Can immediately halt all agent execution or lock to read-only
- **Manga-style storyboard generator** (402 lines): HTML→PNG via Puppeteer with visual narrative frames
- **Voice message transcription**: Via Gemini 2.5 Flash (speech-to-text)
- **PDF processing**: Text extraction and storyboarding of PDF inputs
- 12 new WhatsApp commands: plan, run, approve, deny, kill, resume, agents, sb

**Database Schema Addition**:
- approval_requests table for tracking pending agent actions
- Agent audit log for all decisions

**Code Organization**:
- src/agents/ directory: agent-store.ts (223 lines), planner.ts, critic.ts, executor.ts, sentinel.ts, lockdown.ts, narrator.ts, storyboard.ts
- src/lib/anthropic-client.ts, gemini-client.ts, github-client.ts
- src/workers/github-poller.ts, nudge-checker.ts

**Architecture Insight**: This phase introduced LangGraph orchestration layer on top of raw API calls, establishing a pattern of chained reasoning with safety checkpoints between each agent.

---

### Pivot Point: v2 Full Rewrite (Mar 5)

**Commit 5: duSraBheja v2: Discord brain — full Python rewrite**
*Mar 5, 06:49:13 -0500 | 6eee7c5*

Complete architectural overhaul (3,928 insertions, 108 files changed):

**Why the pivot?**
- TypeScript/Node was too resource-intensive for always-on collector/worker loop
- WhatsApp API limitations
- Need for native async/await with proper concurrency
- Better integration with Python-based ML/analytics tooling

**New Stack**:
- **Discord.py** bot instead of WhatsApp (channel-per-bucket layout for organizational clarity)
- **Python 3.12+** with async/await native concurrency
- **SQLAlchemy 2.0** (async ORM) instead of raw postgres
- **ARQ** (async job queue, Redis-backed) instead of NATS/Temporal
- **FastAPI** for REST API + private dashboard + public site
- **MCP (Model Context Protocol)** server for Claude Code/Codex integration
- **PostgreSQL + pgvector** (kept from v1 but with schema redesign)
- **Alembic** for database migrations (declarative schema versioning)
- **Docker Compose** deployment (4 services: bot, worker, api, postgres)

**4 AI Agents** (all running in Python):
- **Classifier** (Claude Haiku 4.5): Fast intent classification
- **Clarifier** (Claude): Asks clarifying questions on ambiguous input
- **Librarian** (Claude Sonnet 4.6): Merges classified artifacts into canonical Notes
- **Retriever** (Claude Sonnet 4.6): Answers questions from brain context

**File Extractors** (ARQ async jobs):
- PDF: pymupdf4llm extraction
- Images: OCR via Claude Vision
- Audio: Whisper transcription
- Excel: openpyxl parsing
- Text: raw extraction
- Router: MIME-type based dispatcher

**New Database Tables** (8 core tables):
- artifacts (raw input with provenance)
- classifications (labeled intent + confidence)
- chunks (512-token, 64-token-overlap semantic chunks)
- notes (canonical merged records)
- embeddings (1536-dim OpenAI text-embedding-3-small)
- links (relationships)
- messages (Discord message log)
- sessions (agent conversation context)

**Core Features**:
- Discord #inbox channel for capture (any message/attachment/link)
- /ask command for RAG-style retrieval with vector search
- MCP tools exposed to Claude Code: ask, capture, context, search, story, protocol
- Private dashboard for review/approval (FastAPI)
- Public profile site from approved facts only
- CLI workflows for agent bootstrap/closeout

**Key Design Decisions**:
1. **Story is presentation, not storage** — raw evidence promoted to canonical memory
2. **Public/private split** — allowlist layer prevents accidental exposure
3. **Always-on machine-native** — designed for 24/7 cloud deployment, not simulating human forgetting
4. **Agent-friendly interfaces** — MCP, REST, and CLI for tool integration

This was the fundamental pivot that enabled the subsequent rapid iteration. Python's async ecosystem, better library ecosystem, and tighter integration with Claude made the next phases possible.

---

### Story-First Brain Era (Mar 11-18)

#### Sub-phase 1: Core API & Collector Foundation (Mar 11, 12:18-14:37)

**Commit 6: Add story-first brain API and deploy tooling**
*Mar 11, 12:18:45 -0400 | 732c78b*

(2,287 insertions, 42 files) — Establishes API layer, story-first memory model, and collector infrastructure:

**Story-First Schema** (alembic migration 002):
- Story is presentation layer for canonical library
- Canonical library is source of truth for facts
- Agents read from and write to different layers

**API Layer** (FastAPI routes):
- src/api/routes/brain.py: 112 lines exposing brain introspection endpoints
- POST /brain/ingest: Accept new evidence
- GET /brain/story: Retrieve narrative view
- POST /brain/reminder: Set reminder for future action

**Background Collector** (src/collector/main.py, 210+ lines):
- Runs on a schedule (5am + 5pm via launchd)
- Scans local file system for projects, git repos
- Extracts git history signals (commits, authors, file changes)
- Uploads to brain via SSH tunnel for processing
- Timeout handling for stuck git probes (git can hang)

**Collector Bootstrap Infrastructure**:
- launchd plist configuration (ops/launchd/com.dusrabheja.collector.plist)
- SSH tunnel relay for secure upload
- Batch processing to avoid rate limits
- scripts/run_collector.sh wrapper

**Services Layer**:
- src/services/story.py: Story generation from raw notes
- src/services/digest.py: Daily digest synthesis
- src/services/sync.py: Sync raw artifacts to canonical memory

This commit established the pattern: **raw evidence ingestion → classification → chunking & embedding → librarian merging → story generation → narrative presentation**. The collector would become the bridge between local work context and the always-on brain.

---

**Commits 7-14: Micro-fixes and refinements** (Mar 11, 12:21-14:37)

- **Hatch packaging**: Fixed src layout configuration for proper distribution
- **FastMCP startup**: Fixed streamable-http initialization
- **Discord bootstrap**: Aligned channels with story-first layout
- **Local collector helpers**: Added tunnel setup and runner scripts (scripts/open_collector_tunnel.sh, scripts/run_collector.sh)
- **Collector batching**: Implemented batch processing with timeouts
- **Git reliability**: Timeout stuck git probes (git can hang on certain repos)
- **Launchd integration**: Pointed collector job at correct workspace path
- **Worker dispatch naming**: Fixed ARQ job naming consistency

These commits show careful operations work: the system needed reliable background collection, proper secrets management (SSH tunnels), and resilience to hanging processes.

---

#### Sub-phase 2: Discord Integration & Planner Pipeline (Mar 11, 14:49-15:47)

**Commit 15: Add Discord ingest receipts and inbox backfill**
*Mar 11, 14:49:10 -0400 | 2c36deb*

(285 insertions, 6 files) — Adds Discord artifact tracking and batch historical ingestion:

- Discord channel now tracks ingest receipts (confirmation messages for processed artifacts)
- Backfill script to reprocess historical Discord messages
- Spinner receipts: initial ⏳ → final emoji reaction when complete
- Prevents duplicate processing via discord_message_id uniqueness constraint

**Commit 16: Fix planner ingest receipts and replay**
*Mar 11, 15:04:13 -0400 | abf4c0b*

(1,093 insertions, 12 files) — Major addition of planner service for intelligent digest/action planning:

- **src/services/planner.py** (328 lines): Claude-powered plan generation from digest
- **src/lib/llm_json.py** (74 lines): Robust JSON extraction from Claude responses with fallback parsing
- Enhanced classifier to validate extracted JSON
- Expanded librarian task to handle planner outputs
- Tests: test_planner.py, test_llm_json.py with edge cases

The planner service bridges digests and actionable plans. This is where the "story-first" concept meets agency — the system doesn't just remember, it synthesizes plans from memory.

---

**Commits 17-18: Polish (Mar 11, 15:06-15:47)**

- Fixed Discord artifact event publishing to correct channels
- Added planner cleanup with OCR replay for handwritten notes
- Discord rate limiting handled during bulk cleanup operations

---

#### Sub-phase 3: Brain Intake, Vector Search, & Project Awareness (Mar 12, 04:51-05:08)

**Commit 19: Improve brain intake and Discord feedback**
*Mar 12, 04:51:39 -0400 | c9cde30*

(536 insertions, 18 files) — Expands extractors, improves feedback loop:

- **New extractors**: DOCX (Word documents), Link extraction (URL metadata parsing)
- Link extractor: Fetches URL title, description, image for semantic enrichment
- Enhanced Discord feedback: Richer status messages for end-user awareness
- Collector now relays to brain API for synchronous feedback

**Commit 20: Fix ask-brain vector search**
*Mar 12, 05:00:12 -0400 | d630372*

(42 insertions, 3 files) — Critical retrieval fix:

- Fixed asyncpg query binding for vector similarity search
- Vector search now correctly ranks by cosine distance
- Test coverage: test_store.py with vector search assertions

**Commit 21: Improve project-aware retrieval**
*Mar 12, 05:04:36 -0400 | d63c001*

(321 insertions, 2 files) — Project context in answers:

- **src/agents/retriever.py** expansion (292 lines added): Now 315 lines total
- Retriever now accepts project_id parameter
- Ranks chunks by project relevance + semantic similarity
- Tests: test_retriever.py validates project-aware ranking

This trio of commits fixed critical data retrieval functionality. The system needed proper vector search, proper project scoping, and proper feedback loops.

---

**Commit 22: Fix asyncpg retrieval query binding**
*Mar 12, 05:08:33 -0400 | caf7b92*

(23 insertions, 2 files) — Async PostgreSQL query parameter binding:

- Corrected parameter binding for asyncpg (different from psycopg2)
- Vector search queries now execute correctly
- Added query binding tests

---

#### Sub-phase 4: Agent History Sync & Storyteller (Mar 12, 09:23-15:55)

**Commit 23: Add agent history storyteller sync**
*Mar 12, 09:23:20 -0400 | a8efe66*

(2,154 insertions, 31 files) — Major feature: agent-to-memory feedback loop:

**Agent History Collection** (src/collector/agent_history.py, 635 lines):
- Parses Claude Code session logs from ~/.claude/projects/
- Extracts: project hints, agent kind, session summaries, tool uses
- Stores as canonical episodes with rich metadata
- Maintains conversation session linkage (for context continuity)

**Storyteller Agent** (src/agents/storyteller.py, 119 lines):
- Processes agent history
- Generates narrative summaries of agent sessions
- Creates synthesis records for learning extracted during coding

**Services**:
- src/services/query.py (347 lines): Query engine for retrieval with project context
- src/services/indexing.py: Vector indexing service
- src/services/digest.py expanded: Now synthesizes from agent history

**Database**:
- 003_agent_history_story_schema.py: Adds tables for agent sessions, episodes, syntheses

**Launchd Integration** (ops/launchd/com.dusrabheja.agent-history.plist):
- Runs every N minutes to sync new agent sessions

This was profound: the brain could now learn from Claude Code sessions. Every time Ahmad used Claude Code, the system captured the context, synthesized it, and stored learnings. This created a feedback loop where agents could inspect their own past work and improve.

---

**Commits 24-27: Storyteller refinement** (Mar 12, 10:02-10:11)

- Improved digest synthesis and planner scope (363 insertions)
- Fixed JSON parsing of storyteller responses with fallback repair
- Digest now correctly synthesizes across multiple project contexts
- Tests: test_storyteller.py, expanded test_digest.py

---

#### Sub-phase 5: Brain Hardening & Private Memory (Mar 12, 11:47-15:51)

**Commit 28: Harden brain state, reminders, and knowledge sync**
*Mar 12, 11:47:44 -0400 | 458f9db*

(2,803 insertions, 30 files) — Comprehensive hardening of memory systems:

**Reminders System** (src/services/reminders.py, 242 lines):
- Time-based and event-based reminders
- Persistent storage with due-date tracking
- Discord notifications when reminder fires
- Background task: src/worker/tasks/reminders.py

**Project State Management** (src/services/project_state.py, 466 lines):
- Snapshot current project state (blockers, next actions, insights)
- Versioned snapshots for change tracking
- Active project ranking (what's Ahmad currently focused on?)
- Brain keeps itself aware of project priorities

**Knowledge System** (src/services/knowledge.py, 198 lines):
- Knowledge base syncing from canonical library
- Fact extraction with certainty ratings
- Linked to source evidence for traceability

**Enhanced Story Generation** (src/services/story.py):
- Better narrative flow
- Richer contextualization

**Database** (004_brain_hardening_schema.py):
- reminder table
- project_state_snapshot table
- knowledge_record table
- All with proper indexes and relationships

**API Expansion** (src/api/routes/brain.py, 82 lines added):
- /brain/reminders: GET list, POST set, DELETE clear
- /brain/state: GET current project state
- /brain/knowledge: GET knowledge base
- /brain/sync: Trigger knowledge refresh

This was the hardening phase. The brain needed to maintain state (reminders, project focus, knowledge graph), not just answer questions. The system evolved from pure retrieval to stateful agency.

---

**Commits 29-35: Memory refinement** (Mar 12, 12:10-15:55)

- Fixed librarian note ingestion (test coverage)
- Added private memory foundations with Apple Notes sync (src/collector/apple_notes.py)
- Session bootstrap flows (session_bootstrap.py): Agent rebooting with context
- Private memory isolation (crypto.py for secret encryption)
- Source ingest service (source_ingest.py for life export processing)
- Voice service (voice.py for persona packet generation)
- Project state controls exposed to agents (fresh project awareness)
- Recovery from malformed closeouts
- Active project ranking via freshness scoring
- Cognition service (services/cognition.py): Synthesis of observations into insights

By Mar 12 EOD, the brain had:
- Persistent reminders
- Project state tracking
- Private memory encryption
- Apple Notes integration
- Voice persona packet
- Ability to learn from past agent sessions
- Active project awareness

---

#### Sub-phase 6: Board-First Workflow & Atlas Dashboard (Mar 13-16)

**Commit 36: Ship board-first brain workflow**
*Mar 13, 11:35:17 -0400 | e785ef1*

Narrative boards become primary interface (major UI/UX pivot):

- Daily/weekly narrative boards posted to Discord
- Boards curated from canonical library (not raw artifacts)
- Story-first presentation layer now primary interaction mode
- Tests: comprehensive board generation tests

**Commit 37: Finish board rollout operations**
*Mar 13, 13:10:52 -0400 | 6c1f19c*

Stabilization of board operations:

- Discord channel organization for board posting
- Rate limiting on board refreshes
- Backfill of old stories into board format

---

**Commit 38: Overhaul retrieval, boards, and timezone reliability**
*Mar 15, 09:56:12 -0400 | d62c209*

(Major refactor, multiple files):

**Retrieval Overhaul**:
- Project-focused vector search (prioritize relevant-project chunks)
- Suppress web drift (ignore outdated web content in answers)
- Eval serialization fixes (vectors properly JSON-serializable)

**Board Improvements**:
- Better narrative flow in generated boards
- Timezone-aware scheduling (critical for launchd jobs)
- Consistent local-time story views

---

**Commit 39: Add curated Chrome signal distillation**
*Mar 15, 13:51:42 -0400 | 4855d8e*

(New capability: browser activity ingestion):

- **src/collector/chrome_signals.py**: Monitor Chrome history, tabs, search
- Extract URLs, titles, time spent per domain
- Curate high-signal pages (filter spam/noise)
- Enrich with metadata

**Commit 40: Automate Chrome signal sync via collector**
*Mar 15, 16:26:14 -0400 | 8a9bbaf*

Integrate Chrome signals into collector loop:

- Collector now syncs Chrome activity alongside git projects
- Batch processing to avoid hitting rate limits
- Pruning of legacy low-signal records

---

**Commit 41: Build Brain Atlas dashboard and local-time story views**
*Mar 15, 18:34:13 -0400 | 227cbdb*

(Major UI milestone):

- **Brain Atlas**: Interactive dashboard showing memory topology
- **Local-time story views**: Narrative events rendered in user's timezone
- Dashboard shows: projects, entities, threads, syntheses in graph layout
- Story view: chronological narrative of memory events

This was a UI leap: from CLI to visual dashboard with topology views. Ahmad could now *see* their brain's structure.

---

**Commits 42-49: Atlas refinement** (Mar 16, 04:14-07:44)

- Tightened atlas curation (filter synthetic/low-signal thoughts)
- Added temporal memory paths (time-based navigation)
- Persona packet: synthetic self-profile for agent handoff prompts
- Dashboard login (secure session management)
- Fixed dashboard session auth crashes
- Middleware ordering fixes for FastAPI session handling
- Persona-aware narration for richer story context

Atlas was becoming the primary knowledge interface. The system learned to curate itself, filtering synthetic noise and prioritizing signal.

---

#### Sub-phase 7: Canonical Library & Public Brain (Mar 16, 10:59-16:41)

**Commit 50: Add canonical library and secret vault foundations**
*Mar 16, 10:59:58 -0400 | b5b1706*

(Major feature: allowlist-based public facts):

- **PublicFactRecord**: Allowlisted facts safe for public consumption
- **SecretVault**: Encrypted private facts (isolated from normal retrieval)
- Separation: private brain vs public surface
- Crypto isolation (src/lib/crypto.py) for vault encryption

**Commit 51: Add vault DM intake and cleanup preview**
*Mar 16, 12:09:18 -0400 | 808fe13*

Discord DM-based secret management:

- Discord DMs can be used to store secrets (isolated channel)
- Cleanup preview shows what will be encrypted/removed
- Audit trail for vault operations

---

**Commit 52: Add public brain surface and vault v2**
*Mar 16, 15:11:16 -0400 | 81f182d*

(Public-facing website):

- Public homepage: /
- Public about page: /about
- Public projects page: /projects (from public facts only)
- Public chatbot: /open-brain (conversational interface)
- All data from PublicFactRecord allowlist only

---

**Commits 53-57: Public surface refinement** (Mar 16, 15:20-16:41)

- Fixed public fact seed metadata merge
- Refresh project snapshots before project answers
- Polish public site for root-domain launch
- Stabilize public profile seed refresh
- Trim stale blockers from fresh project state

By end of Mar 16, Ahmad had:
- Private dashboard (atlas) for personal knowledge inspection
- Public website for external consumption
- Strict separation via allowlist
- Encrypted vault for secrets
- Project snapshots for consistent state

---

#### Sub-phase 8: Narrative Website & Autonomy (Mar 17-18)

**Commit 58: Rebuild narrative site and self-knowledge surfaces**
*Mar 17, 15:48:43 -0400 | a7f5c14*

Website rebuild with richer narrative:

- Improved self-knowledge representation
- Narrative-first design (story over raw data)
- Better project contextualization

---

**Commit 59: Overhaul public site design + add conversational digital clone chatbot**
*Mar 18, 06:14:24 -0400 | 409dbc6*

(Major feature: AI conversational interface):

- **Digital clone chatbot**: Conversational AI trained on public facts
- Responds as Ahmad to public inquiries
- Personality-aware (persona packet guides responses)
- Embedded on public site

---

**Commit 60: Curate public site: text-first hero, constrained photos, structured acts, starter chips**
*Mar 18, 06:49:14 -0400 | 3b58fb0*

UX polish:

- Text-first hero section (content before imagery)
- Constrained photo gallery (curated, not dump)
- Structured act descriptions (clear value propositions)
- Starter chips: quick-start prompts for chatbot conversation

---

**Commit 61: Brain-owned website builder: autonomous site management + dark visual identity**
*Mar 18, 09:46:42 -0400 | 3050345*

(Autonomous agent for website management):

- **Website builder agent** (src/agents/website_builder.py): Autonomous site generation
- Brain owns and manages its own website
- Dark visual identity (consistent with personal brand)
- Auto-rebuilds from canonical library updates
- No manual HTML editing — brain generates all pages

**Architecture**:
- Brain monitors PublicFactRecord for changes
- Triggers website rebuild automatically
- Generates semantic HTML from narrative structure
- CSS from brand theme

This was remarkable: the brain became self-managing. It controlled its own external representation.

---

**Commit 62: Add expertise model synthesis + git-aware self-management + fix seed bugs**
*Mar 18, 10:34:53 -0400 | bacaebc*

(Self-knowledge enrichment):

- **Expertise model**: System synthesizes Ahmad's skills from project history
- Git-aware (inspects code commits, PRs, languages used)
- Auto-populates skills section on public site
- Public site seed bugs fixed

The brain now understood Ahmad's expertise by analyzing code artifacts.

---

**Commits 63-64: Final polish** (Mar 18, 11:09-11:11)

- Fix project pages: real links, case studies, interests (kill raw dumps)
- Fix case study slug matching: fuzzy match brain notes to narrative slugs for proper linking

---

## Key Architectural Decisions & Rationale

### 1. **Story is Presentation, Not Storage**
- Raw artifacts (evidence) ingested into database
- Classified, chunked, embedded
- Promoted to canonical memory (Note, Thread, Entity, Synthesis)
- Story generated on-demand from canonical layer
- Rationale: Allows multiple story formats (narrative, boards, chatbot) from single source of truth

### 2. **Public/Private Separation via Allowlist**
- PublicFactRecord table: explicit allowlist
- Public site queries only from allowlist
- Private brain (Atlas) sees everything
- Rationale: Prevent accidental exposure while enabling safe sharing

### 3. **Confidence Gating & Review Queue**
- Classification below 0.75 confidence → human review
- Confidence scores tracked
- Low-confidence items don't automatically promote
- Rationale: Safety — catch ambiguous inputs before they corrupt memory

### 4. **Agent History Feedback Loop**
- Claude Code sessions → captured via collector
- Synthesized into episodes
- Brain learns from its own reasoning sessions
- Rationale: Continuous learning — agent work becomes memory

### 5. **Vector Search for Retrieval**
- 1536-dim OpenAI embeddings on 512-token chunks
- Cosine distance ranking
- Project-aware ranking (boost relevant project)
- Rationale: Semantic retrieval (not keyword matching) for better context in answers

### 6. **Multi-Source Ingestion**
- Discord messages
- Apple Notes
- Chrome activity (history, tabs)
- Git commits (via collector)
- File attachments (PDF, images, Excel, DOCX)
- Rationale: Comprehensive life context capture

### 7. **Always-On Collector**
- Background processes (launchd on macOS)
- Scheduled scans (5am, 5pm, etc.)
- SSH tunneling for secure remote upload
- Rationale: Passive capture without user action

### 8. **Autonomous Website Builder**
- Brain owns its own website
- Auto-regenerates from canonical facts
- No manual HTML editing
- Rationale: Site reflects reality (is living document, not static)

---

## Critical Challenges & How They Were Solved

### Challenge 1: Resource Consumption (Feb 24)
**Problem**: TypeScript multi-process architecture (10+ child processes) consuming excessive memory
**Solution**: Merged to single Node process, compiled to JS, capped heap at 256MB
**Commit**: 2ef1194

### Challenge 2: Type Safety & Ecosystem (Mar 5)
**Problem**: Node ecosystem limitations for always-on collection, async concurrency
**Solution**: Complete rewrite to Python 3.12 with native async/await, better libraries (SQLAlchemy, ARQ, MCP)
**Commit**: 6eee7c5

### Challenge 3: Vector Search Correctness (Mar 12)
**Problem**: asyncpg parameter binding for vector queries was incorrect, search returning wrong results
**Solution**: Fixed parameter binding, wrote test cases for vector similarity
**Commits**: d630372, caf7b92

### Challenge 4: Agent Session Capture (Mar 12)
**Problem**: Brain had no way to learn from Claude Code usage
**Solution**: Collector scans ~/.claude/projects/, synthesizes sessions into episodes
**Commit**: a8efe66

### Challenge 5: Public/Private Boundary (Mar 16)
**Problem**: Risk of accidentally exposing private facts to public site
**Solution**: Explicit PublicFactRecord allowlist, strict query filtering
**Commits**: b5b1706, 81f182d

### Challenge 6: Git Hanging (Mar 11)
**Problem**: `git status` or `git log` could hang indefinitely on problematic repos
**Solution**: Timeout wrapper (30 seconds), graceful handling
**Commit**: fd601f0

### Challenge 7: Discord Rate Limiting (Mar 11)
**Problem**: Batch backfill operations hitting Discord API limits
**Solution**: Rate limit awareness, retry with backoff
**Commit**: d9de5dd

### Challenge 8: Project Context in Retrieval (Mar 12)
**Problem**: Answers not scoped to user's current project context
**Solution**: Project-aware vector ranking, project_id parameter on retrieval queries
**Commit**: d63c001

---

## Code Metrics & Scale

- **Total commits**: 92 over 23 days (4/day average, with sprint days at 10+)
- **Languages**: TypeScript (Phase 1-3), Python (Phase v2 onward)
- **Lines of code**: ~30K across src/ directory by final commit
- **Database tables**: 8 core tables in v2 (Artifact, Classification, Chunk, Note, Link, Entity, Thread, Synthesis)
- **Services**: 15+ services (digest, story, planner, query, knowledge, reminders, cognition, etc.)
- **Agents**: 4 AI agents (Classifier, Clarifier, Librarian, Retriever, Storyteller, WebsiteBuilder)
- **Extractors**: 6 file type extractors (PDF, Image, Audio, Excel, DOCX, Link, Text)
- **Tests**: 30+ test files covering critical paths

---

## Evolution of AI Models Used

| Phase | Classifier | Planner | Critic | Executor | Notes |
|-------|-----------|---------|--------|----------|-------|
| Phase 1 | Ollama (llama3.1:8b) | N/A | N/A | N/A | Local inference |
| Phase 2 | Ollama | Claude Sonnet | Gemini 2.5 Pro | Claude Sonnet | Mixed cloud/local |
| Phase v2+ | Claude Haiku 4.5 | Claude Sonnet 4.6 | N/A | N/A | Cloud-only, cost optimized |
| Final | Haiku 4.5 | Sonnet 4.6 + GPT-4o Web | N/A | N/A | Web access for research |

---

## Deployment & Operations

**Deployment Architecture** (by Mar 18):
- PostgreSQL 16 (primary store)
- Redis (ARQ job queue)
- FastAPI (REST API on :8000)
- Discord bot (running 24/7, listening for messages)
- ARQ worker (processing jobs asynchronously)
- MCP server (exposed to Claude Code)

**Background Jobs**:
- Collector sync: 5am, 5pm via launchd
- Agent history sync: Every 5 minutes
- Daily digest: 8am
- Knowledge refresh: Every 6 hours
- Voice refresh: Every 5 hours
- Reminders: Continuous polling
- Website rebuild: On PublicFactRecord change

**Scaling Insights**:
- Single-process worker architecture (ARQ is lightweight)
- PostgreSQL pgvector handles 1536-dim embeddings efficiently
- Redis job queue prevents blocking
- Async/await throughout (no blocking I/O)
- Collector runs on personal machine (doesn't hit cloud quota)

---

## Key Learnings & Patterns

### 1. **Architecture Pivot Was Necessary**
The TypeScript foundation was sound (good infrastructure, proper separation), but hitting resource/library limits. Python rewrite enabled faster iteration because:
- Native async/await (not callback-based)
- Rich ML/analytics library ecosystem
- Better MCP integration
- Simpler deployment

### 2. **Story-First Design Worked**
Decoupling storage (canonical library) from presentation (story/board/chatbot) enabled:
- Multiple interfaces to same data
- Easy to add new presentation formats
- Reduced data duplication
- Clear semantic model

### 3. **Feedback Loops Drive Intelligence**
- Agent history → captured → synthesized → stored → accessible
- This loop meant brain constantly improved as Ahmad used Claude Code
- Without feedback loop, brain would be static

### 4. **Allowlist Model for Privacy**
Public/private split via explicit allowlist (not blacklist) was safer:
- Default deny (only public facts appear on public site)
- Opt-in sharing
- Easy to audit what's exposed

### 5. **Background Collection > Interactive Capture**
- Collector (automated) ingests more signal than explicit capture
- Git history, Chrome activity, Apple Notes were all passive
- User had to do zero extra work to feed brain

### 6. **Autonomous Site Management**
Website builder agent that owns its own representation was powerful:
- No manual content management overhead
- Site always reflects current state
- Brand consistency maintained automatically

---

## Timeline Overview

```
Feb 24 (Day 1):     Phase 0-3 complete (TypeScript foundation + multi-agent swarm)
                    ├─ 15:37 Commit 1: Foundation
                    ├─ 15:56 Commit 2: Self-chat filter + CONNECTION_DRAINING
                    ├─ 16:32 Commit 3: Single-process + Chromium optimization
                    └─ 19:00 Commit 4: LangGraph swarm + approval gates

Mar 5 (Day 10):     Python v2 Rewrite
                    └─ 06:49 Commit 5: Complete Discord bot rewrite

Mar 11 (Day 16):    Story-First API + Collector
                    ├─ 12:18 Story-first schema + API routes
                    ├─ 12:21-14:37 Eight micro-commits (infrastructure polish)
                    ├─ 14:49 Discord ingest receipts
                    ├─ 15:04 Planner service
                    └─ 15:47 Cleanup + OCR replay

Mar 12 (Day 17):    Brain Intake, Vector Search, Agent Sync (48+ insertions per commit)
                    ├─ 04:51 Link extraction + DOCX support
                    ├─ 05:00 Vector search fixes
                    ├─ 05:04 Project-aware retrieval
                    ├─ 05:08 asyncpg binding fixes
                    ├─ 09:23 Agent history storyteller (2,154 insertions!)
                    ├─ 10:02 Storyteller refinement
                    └─ 11:47 Brain hardening (2,803 insertions)

Mar 13 (Day 18):    Board-First Workflow
                    ├─ 11:35 Board rollout
                    └─ 13:10 Finish operations

Mar 15 (Day 20):    Retrieval Overhaul, Chrome Signals, Atlas Dashboard
                    ├─ 09:56 Retrieval + timezone reliability
                    ├─ 13:51 Chrome signal distillation
                    ├─ 16:26 Automated Chrome sync
                    └─ 18:34 Brain Atlas dashboard (major UI milestone)

Mar 16 (Day 21):    Public Brain Surface
                    ├─ 10:59 Canonical library + secret vault
                    ├─ 15:11 Public website
                    └─ 16:41 Public surface polish

Mar 17 (Day 22):    Narrative Website
                    └─ 15:48 Rebuild with self-knowledge

Mar 18 (Day 23):    Autonomous Website Builder + Digital Clone
                    ├─ 06:14 Digital clone chatbot
                    ├─ 06:49 Site curation (text-first hero)
                    ├─ 09:46 Autonomous website builder (LANDMARK)
                    ├─ 10:34 Expertise model synthesis
                    └─ 11:11 Final polish
```

---

## Final Architecture (Mar 18, 11:11)

```
LOCAL COLLECTION (always-on, launchd):
  Collector → [Git commits, Apple Notes, Chrome activity, File system]
             → SSH upload → Brain

DISCORD INTAKE (24/7 bot listening):
  Discord messages/attachments → Bot → ARQ job queue
                                        ↓
                                    Extract (PDF/image/audio/Excel/DOCX/link)
                                    ↓
                                    Classify (Haiku 4.5)
                                    ↓
                                    Embed (OpenAI 1536-dim)
                                    ↓
                                    Librarian merge (Sonnet 4.6)
                                    ↓
                                    Canonical library (Note/Entity/Thread/Synthesis)

CANONICAL LIBRARY (source of truth):
  Evidence → Observations → Episodes → Threads → Entities → Syntheses
  + Project state snapshots
  + Reminders
  + Knowledge graph

STORY GENERATION (on-demand):
  Canonical library → [Digest, Boards, Narratives, Chatbot personality]

PRIVATE INTERFACE:
  Atlas Dashboard (FastAPI) + MCP tools → Claude Code/Codex integration

PUBLIC INTERFACE:
  Website builder agent → Auto-generates site from PublicFactRecord allowlist
  Digital clone chatbot → Conversational Q&A from public facts

BACKGROUND JOBS (continuous):
  • Digest synthesis (8am)
  • Knowledge refresh (every 6h)
  • Cognition (every 4h)
  • Voice refresh (every 5h)
  • Reminders polling (continuous)
  • Website rebuild (on change)
  • Collector sync (5am, 5pm)
  • Agent history sync (every 5min)
```

---

## Conclusion

duSraBheja evolved from a TypeScript WhatsApp bot (Feb 24) to a comprehensive Python-based Discord Brain with autonomous website management and digital clone chatbot (Mar 18) in 92 commits over 23 days.

**Key innovations**:
1. **Story-first architecture**: Storage (canonical library) separate from presentation (story/board/chatbot)
2. **Feedback loops**: Agent work (Claude Code sessions) captured and synthesized
3. **Always-on collection**: Background processes feeding context without user action
4. **Public/private split**: Allowlist-based privacy model
5. **Autonomous agency**: Website builder that owns its own representation

**By end of Mar 18**:
- Captures evidence from 6+ sources (Discord, Apple Notes, Chrome, Git, files, life exports)
- Classifies with Haiku 4.5 (0.75 confidence gate)
- Promotes to canonical memory via Sonnet 4.6 librarian
- Serves private dashboard (Atlas) for inspection
- Serves public website auto-generated from allowlist
- Exposes to Claude Code via MCP tools
- Runs 24/7 with background collection and synthesis
- Digital clone answers public questions as Ahmad

The system is fully production-capable and deployed.
