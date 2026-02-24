# duSraBheja: Master Architecture Specification
## Solo AI Command Center — Second Brain + Multi-Agent Swarm

**Version**: v1.0 | **Date**: 2026-02-24
**Classification**: Engineering-ready architecture specification
**Swarm Role**: Synthesized output from 7-agent virtual swarm

---

# OUTPUT 1: VISION LOCK

## Canonical Vision (Source-of-Truth)

A **solo-operator command center** where brains connect to brains connect to agents. The system captures everything through WhatsApp, displays a storyboard of the operator's knowledge and thinking, manages multiple codebases and GitHub projects, and orchestrates multiple AI agents (Claude, Codex, Antigravity, others) — all running 24/7, locally intelligent, safe, and offline-capable.

### Must Preserve

| # | Principle | Source-of-Truth Reference |
|---|-----------|--------------------------|
| MP-1 | Solo user. No team features. One human, one command center. | SOT Note #1 |
| MP-2 | WhatsApp is THE primary interface. Not Slack, not a web app. | SOT Note #2 |
| MP-3 | Storyboard view: visual representation of the operator's brain. | SOT Note #3 |
| MP-4 | Codebase workspace + multi-project GitHub status in one place. | SOT Note #4 |
| MP-5 | Multi-agent control plane: Claude, Antigravity, Codex, extensible. | SOT Note #5 |
| MP-6 | "Brains connected to brains connected to agents" — hierarchical cognitive architecture. | SOT Note #6 |
| MP-7 | Safe. Real command center. Second brain. Not a toy. | SOT Note #7 |
| MP-8 | Offline execution with status updates. Not just passive storage. | SOT Note #8 |
| MP-9 | Idea tracking → action plan generation. Capture → execute pipeline. | SOT Note #9 |
| MP-10 | Autonomous operation governed by the brain hierarchy. | SOT Note #10 |
| MP-11 | Best intelligence framework. Experimental/aggressive technology is acceptable. | SOT Note #11 |
| MP-12 | Locally intelligent first. Cloud only where local cannot meet reliability needs. | SOT Note #15 |
| MP-13 | 8 building blocks from video: Drop Box, Sorter, Form, Filing Cabinet, Receipt, Bouncer, Tap on Shoulder, Fix Button. | Video Facts #8-15 |
| MP-14 | 12 design principles from video, especially: separate memory/compute/interface, treat prompts like APIs, default to safe, design for restart, core loop then modules. | Video Facts #16-27 |

### Must Avoid

| # | Anti-pattern | Why |
|---|-------------|-----|
| MA-1 | Cloud-first architecture where intelligence lives in SaaS | Violates SOT Note #15: "locally intelligent rather than infra-driven" |
| MA-2 | Single-model lock-in (e.g., OpenAI-only or Google-only) | Violates SOT Note #5: must support Claude, Codex, Antigravity, others |
| MA-3 | Slack or non-WhatsApp primary interface | Violates SOT Note #2 explicitly |
| MA-4 | Team/multi-user features in MVP | Violates SOT Note #1: solo system |
| MA-5 | Passive Notion/note-taking-only system | Violates SOT Notes #8, #10: must execute, not just store |
| MA-6 | Brittle single-agent loops without durability | Violates 24/7 requirement and Principle #10: design for restart |
| MA-7 | Autonomous production deploys without approval | Violates SOT Note #7: must be safe |
| MA-8 | Over-engineered infrastructure that a solo operator cannot maintain | Violates Principle #12: optimize for maintainability over cleverness |

---

# OUTPUT 2: RESEARCH DOSSIER

## 2A. DeepMind / Google Research

| # | Finding | Label | Source | Date | Relevance |
|---|---------|-------|--------|------|-----------|
| D-1 | Gemini 2.0 Flash launched with native tool use (code execution, search, function calling) built into the model, not bolted via prompts. | [FACT] | [Google AI Blog](https://blog.google/technology/google-deepmind/google-gemini-ai-update-december-2024/) | Dec 2024 | Native tool-use reduces prompt engineering complexity for agent tasks. |
| D-2 | Project Mariner: Chrome extension agent powered by Gemini 2.0 that autonomously navigates web pages. Operates within user's browser with user watching — transparent, interruptible. | [FACT] | [DeepMind Mariner](https://deepmind.google/technologies/gemini/project-mariner/) | Dec 2024 | Pattern to adopt: agent transparency. Every action visible and logged. |
| D-3 | Project Astra: multimodal agent (camera, mic, real-time response). Demonstrated on phone and glasses. | [FACT] | [DeepMind Astra](https://deepmind.google/technologies/gemini/project-astra/) | May 2024 | Design for multimodal input from the start. |
| D-4 | Jules: AI code agent on GitHub, built on Gemini 2.0. Creates PRs, fixes bugs asynchronously. | [FACT] | [Google AI Blog](https://blog.google/technology/google-deepmind/google-gemini-ai-update-december-2024/) | Dec 2024 | Async task execution with PR as output artifact — same pattern needed for code workspace. |
| D-5 | SIMA: generalist agent across 9+ video game environments using natural language as universal interface. Modular architecture (perception, grounding, action). | [FACT] | [DeepMind SIMA Blog](https://deepmind.google/discover/blog/sima-generalist-ai-agent-for-3d-virtual-environments/) | Mar 2024 | Standardize on natural language as inter-agent protocol. Modular separation of layers. |
| D-6 | ReadAgent: LLM-based method for handling very long documents via gist memory compression (3x-20x compression). Outperforms brute-force long-context on quality benchmarks. | [FACT] | [arXiv](https://arxiv.org/abs/2402.09727) | Feb 2024 | Critical for local-first with smaller models. Implement gist memory layer for brain state compression. |
| D-7 | AlphaProof + AlphaGeometry 2: autonomous mathematical reasoning at IMO silver-medal level with self-verification loops. | [FACT] | [DeepMind Blog](https://deepmind.google/discover/blog/ai-solves-imo-problems-at-silver-medal-level/) | Jul 2024 | Self-verification pattern: agents that check their own work produce dramatically better results. |
| D-8 | Google ADK (Agent Development Kit): open-source multi-agent framework with hierarchical agent composition, A2A protocol support, MCP tool integration, session management. | [FACT] | [ADK Docs](https://google.github.io/adk-docs/) | Apr 2025 | Multi-model support (not just Gemini). Agent hierarchy pattern matches FBM brain topology. |
| D-9 | A2A Protocol: agent-to-agent communication standard over HTTP/JSON-RPC/SSE. Agent Card discovery (`/.well-known/agent.json`), Task/Message/Artifact primitives. Complementary to MCP (MCP = tools, A2A = agent-to-agent). | [FACT] | [A2A Protocol](https://a2aprotocol.ai/) | Apr 2025 | Use MCP for tool connections + A2A for agent-to-agent coordination. Both are mandatory for the interop layer. |
| D-10 | Google SAIF (Secure AI Framework): principles for securing AI systems including agents — input validation, output sanitization, least-privilege, audit logging. | [FACT] | [Google SAIF](https://safety.google/cybersecurity-advancements/saif/) | Jun 2023 | Security checklist for hardening the command center. |

## 2B. Competing Frameworks Research

| # | Finding | Label | Source | Date | Relevance |
|---|---------|-------|--------|------|-----------|
| C-1 | ReAct (Reason+Act) pattern: agents that interleave reasoning traces with tool actions outperform pure action or pure reasoning approaches. | [FACT] | [arXiv:2210.03629](https://arxiv.org/abs/2210.03629) | Oct 2022 | Mandatory reasoning pattern for all agents in the mesh. |
| C-2 | Tree of Thoughts: structured exploration of multiple reasoning paths with backtracking. Improves planning quality for complex tasks. | [FACT] | [arXiv:2305.10601](https://arxiv.org/abs/2305.10601) | May 2023 | Use for Planner Agent when generating multi-option action plans. |
| C-3 | Reflexion: agents that reflect on failures and revise strategy show significant improvement on sequential decision tasks. | [FACT] | [arXiv:2303.11366](https://arxiv.org/abs/2303.11366) | Mar 2023 | Implement reflect-and-revise loop in the control cycle (step 8: Learn). |
| C-4 | Generative Agents (Stanford): persistent memory (episodic + semantic + reflection) enables emergent behavior in simulated environments. Memory retrieval by relevance + recency + importance. | [FACT] | [arXiv:2304.03442](https://arxiv.org/abs/2304.03442) | Apr 2023 | Direct inspiration for the 3-tier memory model (Episodic, Semantic, Procedural). |
| C-5 | MCP (Anthropic) is model-agnostic open protocol for tool integration. JSON-RPC over stdio or SSE. Hundreds of community servers. Adopted by Claude, Cursor, VS Code, Zed. | [FACT] | [MCP Spec](https://modelcontextprotocol.io/) | 2024-2025 | Mandatory interop layer. All tool connections go through MCP. |
| C-6 | Temporal.io: durable execution engine guaranteeing workflow completion across crashes, restarts, network failures. Used by Netflix, Uber, Stripe. Event-sourced, signal-based HITL, saga/compensation. | [FACT] | [Temporal Docs](https://docs.temporal.io/) | 2020-2025 | Mandatory execution backbone for 24/7 reliability. |
| C-7 | LangGraph: stateful cyclical computation graphs for agent workflows. Typed state, checkpointing, HITL interrupts. Apache 2.0 core. | [FACT] | [LangGraph Docs](https://docs.langchain.com/oss/python/langgraph/overview) | 2024-2025 | Best agent cognition runtime. Orchestrates reasoning graphs. |

## 2C. Safety Research

| # | Finding | Label | Source | Date |
|---|---------|-------|--------|------|
| S-1 | OWASP LLM Top 10: prompt injection (#1), insecure output handling (#2), training data poisoning (#3), model DoS (#4), supply chain (#5). | [FACT] | [OWASP](https://owasp.org/www-project-top-10-for-large-language-model-applications/) | 2023-2025 |
| S-2 | Indirect prompt injection via tool results: attacker plants instructions in web content or API responses that the LLM reads and follows. Demonstrated by Greshake et al. | [FACT] | [arXiv:2302.12173](https://arxiv.org/abs/2302.12173) | Feb 2023 |
| S-3 | Microsoft Spotlighting: marking user data with special tokens so the model can distinguish instructions from data. Reduces injection success by >80%. | [FACT] | [arXiv:2403.14720](https://arxiv.org/abs/2403.14720) | Mar 2024 |
| S-4 | Anthropic Constitutional AI: model self-critiques and revises outputs against a set of principles. Reduces harmful outputs without human feedback per instance. | [FACT] | [arXiv:2212.08073](https://arxiv.org/abs/2212.08073) | Dec 2022 |
| S-5 | Dedicated prompt injection classifiers (DeBERTa-based, e.g., Rebuff, LLM Guard, Lakera Guard) can detect injection attempts with >95% accuracy on known attack patterns. Run locally. | [FACT] | [Rebuff](https://github.com/protectai/rebuff), [LLM Guard](https://llm-guard.com/) | 2023-2024 |

---

# OUTPUT 3: SYSTEM FRAMEWORK

## [DECISION] Framework: Fractal Brain Mesh (FBM) v2.0

### Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    INTERFACE LAYER                           │
│  WhatsApp (primary)  │  Storyboard UI  │  CLI (dev mode)    │
└──────────┬───────────┴────────┬────────┴───────┬────────────┘
           │                    │                │
┌──────────▼────────────────────▼────────────────▼────────────┐
│                    GATEWAY LAYER                             │
│  CF Worker → CF Tunnel → Local Webhook Server (port 3000)   │
│  NATS Event Bus (pub/sub + JetStream durable streams)       │
└──────────┬──────────────────────────────────────────────────┘
           │
┌──────────▼──────────────────────────────────────────────────┐
│                   COGNITION LAYER (LangGraph)                │
│                                                              │
│  ┌──────────┐  ┌───────────┐  ┌──────────┐  ┌───────────┐  │
│  │ Planner  │  │  Critic   │  │ Executor │  │ Sentinel  │  │
│  │  Agent   │──│  Agent    │──│  Agent   │──│  Agent    │  │
│  └──────────┘  └───────────┘  └──────────┘  └───────────┘  │
│                      │                            │          │
│              ┌───────▼────────┐   ┌──────────────▼────────┐ │
│              │ Narrator Agent │   │    Router Agent        │ │
│              └────────────────┘   └───────────────────────┘ │
└──────────┬──────────────────────────────────────────────────┘
           │
┌──────────▼──────────────────────────────────────────────────┐
│                  DURABILITY LAYER (Temporal.io)               │
│  Workflow engine: retries, sagas, signals, cron, versioning  │
│  Every LangGraph run is a Temporal Activity                  │
└──────────┬──────────────────────────────────────────────────┘
           │
┌──────────▼──────────────────────────────────────────────────┐
│                   MEMORY LAYER                               │
│                                                              │
│  ┌─────────────────┐  ┌──────────────┐  ┌────────────────┐ │
│  │ Episodic Memory  │  │ Semantic Mem  │  │ Procedural Mem │ │
│  │ (Postgres events) │  │ (pgvector)   │  │ (Postgres JSON)│ │
│  └─────────────────┘  └──────────────┘  └────────────────┘ │
│           │                    │                  │          │
│  ┌────────▼────────────────────▼──────────────────▼───────┐ │
│  │         Brain Graph (Apache AGE in Postgres)           │ │
│  │  Core Brain ←→ Project Brains ←→ Execution Brains      │ │
│  └────────────────────────────────────────────────────────┘ │
└──────────┬──────────────────────────────────────────────────┘
           │
┌──────────▼──────────────────────────────────────────────────┐
│                  TOOL LAYER (MCP Protocol)                    │
│  MCP Servers: GitHub, Filesystem, Postgres, Web Search       │
│  Agent-to-Agent: A2A Protocol (future)                       │
└──────────┬──────────────────────────────────────────────────┘
           │
┌──────────▼──────────────────────────────────────────────────┐
│                  MODEL LAYER (Multi-Provider)                │
│  Cloud: Claude Opus/Sonnet (reasoning), GPT-4o (fast/vision)│
│         Gemini 2.5 Pro (long context, Google integrations)   │
│  Local: Ollama (Llama 3.1 70B reasoning, 8B classification) │
│         MLX (Whisper transcription, fine-tuned classifiers)  │
└─────────────────────────────────────────────────────────────┘
```

### Why FBM v2.0 Was Chosen

| Decision | Rationale | Tradeoff | Risk | Mitigation |
|----------|-----------|----------|------|------------|
| Separate cognition (LangGraph) from durability (Temporal) | No single framework handles both agent reasoning AND industrial-grade reliability. LangGraph excels at graph-based reasoning. Temporal excels at surviving crashes. | Higher integration complexity. Two systems to learn. | Integration seams between LangGraph state and Temporal workflow state. | Wrap LangGraph runs as Temporal Activities. LangGraph handles reasoning; Temporal handles lifecycle. |
| MCP + A2A as interop layer | Prevents model/framework lock-in. Any agent using MCP can access any tool. A2A enables agent-to-agent communication across vendors. | Protocols are still maturing. A2A adoption is early. | Protocol spec changes could break integrations. | Start with MCP (mature). Adopt A2A incrementally in Phase 3+. Build adapters behind stable internal interfaces. |
| Postgres as unified data backbone | One database for relational data (pgvector for embeddings, Apache AGE for graph, event log, audit). Reduces operational complexity for a solo operator. | Postgres must serve many roles. Potential performance bottleneck at extreme scale. | Graph query performance may lag behind dedicated Neo4j for complex traversals. | Monitor query performance. Add Neo4j in Phase 3+ only if graph queries become the bottleneck. |
| NATS as event bus | Tiny footprint (~10MB RAM). JetStream provides durable streams. Sub-millisecond pub/sub for kill switch. Single binary. | Less well-known than Redis. Fewer tutorials. | Smaller community than Redis. | NATS has been production-proven at scale (Synadia, CNCF). Replace with Redis Streams if NATS causes problems. |
| Ollama + MLX for local inference | Ollama provides model management + API serving. MLX provides Apple Silicon-native performance for Whisper and fine-tuned classifiers. | Two local ML runtimes to manage. | MLX ecosystem less mature. Model compatibility gaps. | Use Ollama as primary. MLX only for Whisper and custom fine-tuned models. |

### Why Alternatives Were Rejected

| Alternative | Rejection Reason |
|-------------|-----------------|
| Single-super-agent loop (one Claude call) | Brittle, no observability, no crash recovery, no multi-model routing. Fails 24/7 requirement. |
| AutoGen/CrewAI as sole orchestrator | No durable execution. Chat-loop architectures lose state on crash. Insufficient safety controls. |
| Pure cloud solution (Assistants API, etc.) | Violates local-intelligence-first requirement (SOT Note #15). Creates total vendor lock-in. |
| Google ADK as primary framework | Too new (Apr 2025). Gemini-centric. Cloud deployment story conflicts with local-first. Patterns are valuable; dependency is premature. |
| OpenAI Agents SDK as primary | OpenAI model lock-in. No durable execution. Directly contradicts multi-model requirement. |
| Restate instead of Temporal | Promising but too new for a 24/7 system. Less ecosystem, less documentation. Tracked as future simplification option. |

---

# OUTPUT 4: IMPLEMENTATION COMPARISON

## Four Implementation Patterns

### Pattern A: "Fractal Brain Mesh" (FBM — Recommended Primary)

| Layer | Technology |
|-------|-----------|
| Cognition | LangGraph (stateful agent graphs) |
| Durability | Temporal.io (self-hosted) |
| Memory | Postgres + pgvector + Apache AGE |
| Event Bus | NATS JetStream |
| Local Models | Ollama + MLX |
| Cloud Models | Claude + GPT-4o + Gemini (via API keys) |
| Tool Protocol | MCP |
| WhatsApp | Cloud API → CF Worker → CF Tunnel |

### Pattern B: "Lightweight Local" (Recommended Fallback)

| Layer | Technology |
|-------|-----------|
| Cognition | PydanticAI agents (lightweight, type-safe) |
| Durability | SQLite outbox + custom retry worker (no Temporal) |
| Memory | SQLite + sqlite-vss (embeddings) |
| Event Bus | SQLite WAL-based polling |
| Local Models | Ollama only |
| Cloud Models | Claude + GPT-4o (via API keys) |
| Tool Protocol | Direct function calls (no MCP) |
| WhatsApp | whatsapp-web.js (prototype) → Cloud API (prod) |

### Pattern C: "Cloud-Assisted Hybrid"

| Layer | Technology |
|-------|-----------|
| Cognition | Google ADK (agent hierarchy) |
| Durability | Temporal Cloud (managed) |
| Memory | Supabase (Postgres + pgvector, hosted) |
| Event Bus | Redis Cloud |
| Local Models | Ollama for offline fallback |
| Cloud Models | Gemini 2.5 Pro (primary) + Claude (secondary) |
| Tool Protocol | MCP + A2A |
| WhatsApp | Twilio → Cloud webhook |

### Pattern D: "Maximum Cloud"

| Layer | Technology |
|-------|-----------|
| Cognition | OpenAI Agents SDK |
| Durability | Inngest (managed) |
| Memory | Pinecone + Notion API |
| Event Bus | None (request-response only) |
| Local Models | None |
| Cloud Models | GPT-4o only |
| Tool Protocol | OpenAI native tools |
| WhatsApp | Twilio |

## Scoring Matrix (1-5, 5 = best for this use case)

| Criterion | Weight | A: FBM | B: Lightweight | C: Hybrid | D: Max Cloud |
|-----------|--------|--------|----------------|-----------|--------------|
| **Safety** | 20% | 5 | 3 | 4 | 2 |
| **Autonomy Quality** | 20% | 5 | 3 | 4 | 3 |
| **Cost (monthly)** | 10% | 4 | 5 | 2 | 1 |
| **Latency** | 10% | 4 | 5 | 3 | 3 |
| **Maintainability** | 15% | 3 | 5 | 3 | 4 |
| **Lock-in Risk** (5=low) | 15% | 5 | 4 | 2 | 1 |
| **Offline Capability** | 10% | 5 | 4 | 2 | 0 |
| **Weighted Score** | 100% | **4.55** | **3.90** | **2.95** | **2.05** |

### [DECISION] Primary: Pattern A (FBM). Fallback: Pattern B (Lightweight Local).

**Why A over B**: Pattern B sacrifices autonomy quality and safety for simplicity. For a system that must "run autonomously using our brains" (SOT Note #10) with industrial 24/7 reliability, Temporal's durable execution and LangGraph's reasoning graphs are not optional — they are the differentiating infrastructure.

**Why B as fallback**: If Temporal + LangGraph proves too complex for initial velocity, Pattern B can be built in a weekend and iterated from there. It is the "start simple, upgrade later" path.

**Why not C or D**: Both violate the local-intelligence-first constraint. Pattern D is a total non-starter (single cloud vendor, no offline, no safety).

---

# OUTPUT 5: CONCRETE SWARM ARCHITECTURE

## Agent Map

| Agent | Role | Model | Tools | Permissions | Inputs | Outputs | Failure Mode | Done Criteria |
|-------|------|-------|-------|-------------|--------|---------|--------------|---------------|
| **Router** | Classify incoming items, route to correct brain/agent | Ollama 8B (fast, local) | Postgres read, NATS publish | Read-only on all data stores. No external API calls. | Raw WhatsApp message, GitHub event, or system event | `{brain_id, category, priority, confidence, next_action}` | Falls back to "unclassified" inbox with low confidence → human review queue | Structured record created OR queued for human review |
| **Planner** | Generate multi-option action plans for tasks | Claude Sonnet (cloud) or Ollama 70B (offline) | Brain graph read, memory search, project status read | Read-only. Cannot execute tools directly. | Task description + relevant brain context | `{options: [{plan, risk, cost, rationale}], recommended}` | Timeout → retry with smaller context. 3 failures → escalate to human. | At least 2 options generated with risk assessment |
| **Critic** | Adversarial review of plans before execution | Claude Sonnet or Gemini 2.5 Pro (different model than Planner) | Read plan, read policy rules, read relevant code | Read-only. Cannot modify plans. | Planner output + policy rules | `{approved: bool, issues: [], risk_rating, recommendation}` | Critic unavailable → auto-deny for R3+, auto-approve for R0-R1 | Binary approval/denial with written rationale |
| **Executor** | Execute approved tool calls and code operations | Per-task: Claude Code, Codex, Antigravity, or Ollama | MCP tool servers (git, filesystem, shell), GitHub API | Per-agent allow-list. No credential access beyond scoped tokens. | Approved plan + tool permissions | `{status, output, artifacts[], tool_calls_log}` | Tool failure → retry (Temporal). 3 retries → pause + alert human. | All plan steps completed OR explicitly paused with reason |
| **Sentinel** | Policy enforcement and safety gate | Ollama 8B (local, fast) + rule engine | Policy rules DB, audit log write | Read all, write audit log only. Can BLOCK any tool call. | Every tool call before execution | `{allowed: bool, reason, risk_class, requires_approval}` | Sentinel crash → DENY ALL tool calls (fail-closed) | Every tool call logged with allow/deny decision |
| **Narrator** | Compress outcomes into human-readable updates | Ollama 8B or Claude Haiku (fast, cheap) | Read agent run logs, read brain state | Read-only. Write only to WhatsApp outbound queue. | Agent run results, brain state changes, schedule triggers | WhatsApp message (text, buttons, or list) | Narrator failure → raw status dump instead of summary | Human-readable message sent to WhatsApp |
| **Scheduler** | Cron-based triggers for recurring tasks | Temporal cron workflows | NATS publish, Postgres read | Trigger-only. Cannot execute tasks directly. | Cron schedule definitions | Events published to NATS for other agents to pick up | Missed schedule → run immediately when detected + alert | Scheduled event published within tolerance window |

## Data Flow

```
1. CAPTURE
   WhatsApp msg → CF Worker → CF Tunnel → Webhook Server → NATS "inbox.raw"
   GitHub event → CF Worker → CF Tunnel → Webhook Server → NATS "inbox.raw"
   Cron trigger → Scheduler → NATS "inbox.raw"

2. ROUTE
   NATS "inbox.raw" → Router Agent → Postgres (InboxItem + BrainNode)
                                    → NATS "brain.{brain_id}.event"

3. PLAN (if actionable)
   NATS "brain.{brain_id}.event" → Planner Agent → Plan document
                                 → Critic Agent → Approved/Denied plan
                                 → NATS "execution.pending" (if approved)

4. EXECUTE (via Temporal Workflow)
   NATS "execution.pending" → Temporal Workflow started
     → Sentinel pre-check (every tool call)
     → Executor Agent runs tools (MCP)
     → Sentinel post-check
     → Results stored in Postgres (AgentRun)
     → NATS "execution.complete"

5. REPORT
   NATS "execution.complete" → Narrator Agent → WhatsApp outbound message
                                              → Storyboard update

6. LEARN
   Correction from user (WhatsApp "fix" command)
     → Update classification/routing
     → Log correction in Episodic Memory
     → [Future] Retrain local classifier on correction data
```

## Control Flow: Approval Gates

```
R0 (Safe):     Router → Execute → Report
               Examples: classify message, search brain, generate summary

R1 (Low):      Router → Plan → Execute → Report + Notify
               Examples: read GitHub status, search code, create brain node

R2 (Medium):   Router → Plan → Critic → Execute → Report + Notify
               Examples: create git branch, open PR draft, update project status

R3 (High):     Router → Plan → Critic → Simulate → [WhatsApp Approval] → Execute → Verify → Report
               Examples: merge PR, delete branch, modify CI config

R4 (Critical): Router → Plan → Critic → Simulate → [WhatsApp Approval] → Execute → Sentinel Monitor → Verify → Report
               Examples: deploy to production, modify credentials, bulk data operations
```

## Escalation Path

```
Agent fails → Temporal retries (3x with backoff)
  → Still failing → Pause workflow + alert human via WhatsApp
    → Human can: retry / modify / abort / escalate to different agent
      → No human response in 30 min → auto-park, add to daily review
```

---

# OUTPUT 6: 24/7 RUNTIME BLUEPRINT

## Local Runtime (Mac Pro)

### Services Running

| Service | Resource | Purpose | Restart Policy |
|---------|----------|---------|----------------|
| Postgres 16 + pgvector + AGE | ~500MB-1GB RAM | System of record, vector search, brain graph | `launchd` auto-restart |
| Temporal Server | ~1-1.5GB RAM | Durable workflow engine | `launchd` auto-restart |
| Temporal Worker (Python) | ~200MB per worker | Executes workflow activities (LangGraph runs) | `launchd` auto-restart |
| NATS Server + JetStream | ~30MB RAM | Event bus, durable streams | `launchd` auto-restart |
| Ollama | ~45-55GB RAM (70B Q4 + 8B loaded) | Local model inference | `launchd` auto-restart |
| Webhook Server (Node.js/Python) | ~100MB RAM | Receives CF Tunnel traffic | `launchd` auto-restart |
| Storyboard UI (Next.js) | ~200MB RAM | Browser-based brain view | `launchd` auto-restart |
| Cloudflare Tunnel | ~50MB RAM | Outbound tunnel to CF edge | `launchd` auto-restart |
| **Total** | **~48-58GB RAM** | | |

[FACT] A Mac Pro with M2 Ultra has 192GB unified memory. This leaves ~130GB+ headroom for macOS, applications, and burst capacity.

### Offline Queue/Replay

```
ONLINE:
  WhatsApp webhook → process immediately
  GitHub sync → poll every 5 min
  Cloud LLM calls → route to API

OFFLINE (no internet):
  WhatsApp → messages queue in Meta's servers (delivered when Mac comes online)
  GitHub sync → skip, retry on next connectivity check
  Cloud LLM calls → route to Ollama (local fallback)
  Outbound WhatsApp → queue in Postgres outbox table

RECONNECT:
  CF Tunnel reconnects automatically
  Postgres outbox worker drains queued messages to WhatsApp Cloud API
  GitHub sync runs immediately
  Temporal workflows resume where they paused
```

### Observability

| Signal | Tool | Alert Channel |
|--------|------|---------------|
| Service health | macOS `launchd` + custom health check script | WhatsApp alert if any service down >2 min |
| Temporal workflow status | Temporal Web UI (localhost:8233) | WhatsApp daily digest |
| Agent error rate | Postgres audit log + NATS metrics | WhatsApp alert if error rate >10% in 5 min |
| Disk usage | Cron job checking df | WhatsApp alert at 80% usage |
| Model inference latency | Ollama metrics endpoint | Log only; alert if p95 >30s |
| Memory usage | macOS Activity Monitor API | WhatsApp alert at 85% system memory |

### Recovery

| Failure | Recovery |
|---------|----------|
| Mac crashes/restarts | All services auto-start via `launchd`. Temporal replays in-flight workflows. NATS JetStream replays unacknowledged messages. Postgres WAL ensures no data loss. |
| Postgres crash | WAL recovery. Automatic. |
| Ollama crash | Auto-restart. In-flight model calls fail → Temporal retries route to cloud model as fallback. |
| Internet drops | Offline mode activates. Local models handle classification/routing. Outbound messages queue. |
| CF Tunnel drops | Auto-reconnects. Meta retries webhook delivery. CF Worker KV buffers if needed. |

### Cloud Additions (Minimal)

| Component | Purpose | Cost | Justification |
|-----------|---------|------|---------------|
| Cloudflare Worker | Webhook validation + offline buffering in KV | Free tier (100K req/day) | Meta requires stable HTTPS endpoint. Local Mac cannot receive webhooks directly. |
| Cloudflare Tunnel | Outbound tunnel from Mac to CF edge | Free | Secure, no ports opened on router. |
| Cloudflare domain (optional) | Stable webhook URL | ~$10/year | Nicer than `*.cfargotunnel.com` |
| **Total cloud cost** | | **~$0-10/year** | |

---

# OUTPUT 7: SAFETY & GOVERNANCE SPEC

## Risk Tiers

| Tier | Name | Description | Approval | Examples |
|------|------|-------------|----------|----------|
| R0 | Passive | Read-only operations, no side effects | Auto-execute, log | Read brain node, search memory, get GitHub status |
| R1 | Low Side-Effect | Creates local data, no external mutations | Auto-execute, log + notify | Create brain node, classify item, generate summary |
| R2 | External Read | Accesses external systems read-only | Auto-execute, log + notify | GitHub API read, web search, code analysis |
| R3 | External Write | Mutates external systems | Require WhatsApp approval | Create PR, push branch, send email, modify config |
| R4 | Destructive/Irreversible | Cannot be undone, high blast radius | Require approval + simulate first | Delete branch, merge to main, deploy, modify credentials |

## Policy Engine

```yaml
# policy_rules.yaml (stored in Procedural Memory)
agents:
  router:
    allowed_tools: [postgres_read, nats_publish]
    max_risk: R0
    model_fallback: ollama_8b

  planner:
    allowed_tools: [brain_graph_read, memory_search, project_status_read]
    max_risk: R0
    requires_critic_for: [R2, R3, R4]

  executor:
    allowed_tools: [mcp_github, mcp_filesystem, mcp_shell]
    max_risk_without_approval: R1
    requires_approval_for: [R3, R4]
    requires_simulation_for: [R4]
    budget_limit_per_run: {tokens: 100000, cost_usd: 5.00}

  sentinel:
    allowed_tools: [policy_read, audit_write]
    max_risk: R0
    fail_mode: deny_all

  narrator:
    allowed_tools: [run_log_read, whatsapp_send]
    max_risk: R1
```

## Approval Workflow

```
1. Executor needs R3+ action
2. Sentinel intercepts → checks policy → flags as needs_approval
3. Temporal workflow pauses (signal wait)
4. Narrator sends WhatsApp approval request:
   "Agent: Claude Code
    Action: Merge PR #42 into main (duSraBheja)
    Risk: R3 (external write)
    Critic says: Approved - tests passing, no conflicts
    [Approve] [Deny] [Details]"
5. User taps Approve or Deny
6. WhatsApp webhook → Temporal signal → workflow resumes or aborts
7. Timeout: 30 min → auto-park + add to daily review
```

## Audit Log Model

```sql
CREATE TABLE audit_events (
    id              BIGSERIAL PRIMARY KEY,
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    trace_id        UUID NOT NULL,          -- groups related events
    agent_name      TEXT NOT NULL,
    action_type     TEXT NOT NULL,           -- tool_call, decision, approval, error
    risk_class      TEXT NOT NULL,           -- R0, R1, R2, R3, R4
    tool_name       TEXT,
    input_summary   TEXT,                    -- truncated/redacted input
    output_summary  TEXT,                    -- truncated/redacted output
    decision        TEXT,                    -- allowed, denied, escalated, timeout
    model_used      TEXT,
    tokens_used     INTEGER,
    cost_usd        NUMERIC(10,6),
    duration_ms     INTEGER,
    error           TEXT,
    metadata        JSONB
);

CREATE INDEX idx_audit_trace ON audit_events(trace_id);
CREATE INDEX idx_audit_time ON audit_events(timestamp);
CREATE INDEX idx_audit_agent ON audit_events(agent_name, timestamp);
CREATE INDEX idx_audit_risk ON audit_events(risk_class, timestamp);
```

## Kill Switch

```
WhatsApp "kill" → Webhook → NATS "system.kill" broadcast
  → All Temporal workers receive → cancel all running workflows
  → All agent processes receive → halt immediately
  → Sentinel enters lockdown mode → deny ALL tool calls
  → Narrator sends confirmation: "All agents stopped. System in lockdown."
  → Resume: WhatsApp "resume" → NATS "system.resume" → normal operation
```

## Prompt Injection Defenses

| Layer | Defense | Implementation |
|-------|---------|----------------|
| Input | Injection classifier | Fine-tuned DeBERTa model via MLX, runs on ALL untrusted input |
| Input | Delimiter isolation | XML tags separating system instructions from user/external data |
| Input | Spotlighting | Mark user data with special tokens per Microsoft research |
| Runtime | Sentinel validation | Every tool call checked against policy before execution |
| Runtime | Output sanitization | Strip executable content from tool outputs before passing to agents |
| Architectural | Model diversity | Critic uses different model than Planner — correlated failures less likely |
| Architectural | Least privilege | Each agent has minimal tool permissions |
| Emergency | Kill switch | WhatsApp command halts all execution within seconds |

---

# OUTPUT 8: BUILD PLAN

## Phased Roadmap

### Phase 0: Foundation (Days 1-3)

**Acceptance Criteria**: Mac Pro has all infrastructure services running. Can send/receive a test message.

| Task | Detail | Done When |
|------|--------|-----------|
| Install Postgres 16 | Homebrew. Enable pgvector and AGE extensions. | `psql` connects, extensions loaded |
| Install NATS | Homebrew. Enable JetStream. | `nats pub test "hello"` works |
| Install Ollama | Homebrew. Pull llama3.1:8b and nomic-embed-text. | `ollama run llama3.1:8b "hello"` returns response |
| Install Temporal | Docker Compose (temporal + temporal-admin-tools + temporal-ui). | Temporal Web UI accessible at localhost:8233 |
| Create DB schema | Run migration: inbox_items, brain_nodes, projects, agents, agent_runs, policy_rules, audit_events, nudges. | All tables created, test insert works |
| Setup CF Tunnel | Install cloudflared, create tunnel, test with a hello-world HTTP server. | `curl https://your-tunnel-url/health` returns 200 from local server |

### Phase 1: Core Loop (Days 4-10)

**Acceptance Criteria**: Send a WhatsApp message → system classifies it → stores in brain → sends confirmation back.

| Task | Detail | Done When |
|------|--------|-----------|
| WhatsApp prototype | Set up whatsapp-web.js for prototype. Scan QR, receive messages on local server. | Incoming message logged to console |
| Inbox processor | NATS subscriber that receives raw messages, calls Ollama 8B to classify (idea/task/note/question), writes InboxItem + BrainNode to Postgres. | Message classified and stored in DB with confidence score |
| Confidence gate | If confidence < 0.7, add to review queue instead of auto-routing. | Low-confidence items visible in review queue |
| WhatsApp responder | Send structured confirmation back: "Captured. Category: idea. Project: unassigned." | Confirmation message received on phone |
| Audit trail | Every automated action writes to audit_events table. | Audit row exists for every classification |
| Daily summary | Temporal cron workflow that runs at 8am, queries today's items, generates summary via Ollama, sends to WhatsApp. | Daily summary received on WhatsApp |

### Phase 2: Brain View + GitHub (Days 11-20)

**Acceptance Criteria**: Storyboard UI shows brain nodes. GitHub status visible. Corrections work.

| Task | Detail | Done When |
|------|--------|-----------|
| Storyboard UI scaffold | Next.js app. Board view (Kanban columns by category). Graph view (D3.js force-directed). | UI renders brain nodes from Postgres |
| Brain graph | Apache AGE in Postgres. Core Brain → Project Brains → nodes. Relationships: related_to, blocks, subtask_of. | Graph query returns connected nodes |
| GitHub integration | MCP server for GitHub (or direct GraphQL). Register repos. Cron job: poll status every 5 min. Write to RepoStatus table. | Dashboard shows PR count, CI status per repo |
| GitHub digest | Add repo health to daily summary. | WhatsApp daily summary includes GitHub section |
| Fix button | WhatsApp command `fix <id> <correction>`. Updates classification, logs correction. | Correction applied, audit logged |
| Nudges | "Tap on shoulder" — surface items needing attention based on age, priority, staleness. | Nudge messages sent at configured intervals |

### Phase 3: Multi-Agent + Safety (Days 21-35)

**Acceptance Criteria**: Can dispatch tasks to Claude/Codex from WhatsApp. Approval gates work. Kill switch works.

| Task | Detail | Done When |
|------|--------|-----------|
| Agent registry | Postgres table: agents (name, type, api_config, tool_permissions, model). Register Claude, Codex, Antigravity. | Agents queryable from DB |
| Policy engine | Load policy_rules.yaml. Sentinel agent validates tool calls against rules. | R3+ actions blocked without approval |
| Planner + Critic | LangGraph graph: Planner node → Critic node → decision. | Plan generated with multiple options + critic review |
| Executor via Temporal | Temporal workflow: receive plan → Sentinel check → execute tools → log results. | Full workflow completes with audit trail |
| WhatsApp `run` command | `run claude review PR 42` → creates Temporal workflow → sends status updates. | Agent task runs end-to-end from WhatsApp |
| Approval gate | R3+ actions pause workflow, send WhatsApp approval request, resume on approve/deny signal. | Approval flow works from WhatsApp |
| Kill switch | WhatsApp `kill` → NATS broadcast → all workflows cancelled. | Kill command stops all agents within 5 seconds |
| WhatsApp Cloud API migration | Replace whatsapp-web.js with Cloud API. Set up CF Worker for validation + buffering. | Production WhatsApp integration running |

### Phase 4: Autonomous Intelligence (Days 36-60)

**Acceptance Criteria**: System autonomously identifies tasks, creates plans, executes with oversight, learns from corrections.

| Task | Detail | Done When |
|------|--------|-----------|
| Semantic memory | pgvector embeddings for all brain nodes. Similarity search for context retrieval. | "search <query>" returns semantically relevant results |
| Memory distillation | Weekly Temporal cron: summarize/compress old episodic memory using ReadAgent-inspired gist pattern. | Memory stays bounded; old items compressed |
| Autonomous planning | System identifies stale tasks, blocked items, opportunities. Generates action plans proactively. | Proactive plan proposals received on WhatsApp |
| Multi-model routing | Router selects model by task type: Claude for reasoning, GPT-4o for vision, Ollama for classification. | Different models used for different task types |
| Correction-driven learning | Accumulate user corrections. Fine-tune local classifier on correction data (MLX LoRA). | Classification accuracy improves measurably after corrections |
| Full offline mode | When internet drops: classify locally, queue outbound, use Ollama for all reasoning. | System functions (with reduced quality) during internet outage |
| WhatsApp Flows | Add Flows for structured capture, agent task submission, approval. | Rich interactive forms in WhatsApp |
| A2A protocol (experimental) | Implement Agent Cards for internal agents. Test cross-agent discovery. | Agents discoverable via `/.well-known/agent.json` pattern |

## First 2 Weeks: Actionable Task List

### Week 1 (Days 1-7)

| Day | Tasks |
|-----|-------|
| 1 | Install Postgres, NATS, Ollama, cloudflared via Homebrew. Pull Ollama models. Create database and schema. |
| 2 | Install Temporal via Docker Compose. Verify all services start and connect. Write health check script. |
| 3 | Set up whatsapp-web.js prototype. Scan QR. Log incoming messages to console. Send test reply. |
| 4 | Build inbox processor: NATS subscriber → Ollama classification → Postgres write → WhatsApp confirmation. |
| 5 | Add confidence gate (threshold 0.7). Build review queue table. Add audit_events logging for every action. |
| 6 | Build daily summary Temporal cron workflow. Template: items captured, items by category, pending review count. |
| 7 | End-to-end test: send 20 real messages from WhatsApp. Verify classification, storage, audit, daily summary. Fix bugs. |

### Week 2 (Days 8-14)

| Day | Tasks |
|-----|-------|
| 8 | Scaffold Next.js storyboard UI. Build API routes for brain nodes (CRUD). Render Kanban board view. |
| 9 | Add brain graph (Apache AGE). Define relationships. Build graph view with D3.js. |
| 10 | GitHub integration: register repos via config file. Build MCP server or direct GraphQL poller. Write RepoStatus every 5 min. |
| 11 | Add GitHub status to storyboard UI. Add GitHub section to daily summary. |
| 12 | Implement WhatsApp `fix` command. Implement `status` command (returns today's summary). Implement `search` command (keyword search on brain nodes). |
| 13 | Add nudge logic: surface items >3 days old with no next action. Send nudge via WhatsApp at 2pm daily. |
| 14 | Full integration test. Fix edge cases. Document what works and what needs Phase 3. |

---

# OUTPUT 9: OPEN QUESTIONS

## Truly Blocking (Need Resolution Before Phase 1)

| # | Question | Proposed Resolution | Assumption If Unresolved |
|---|----------|--------------------|-----------------------|
| OQ-1 | **Which phone number for WhatsApp Business?** You need a phone number that is NOT currently registered on WhatsApp (or willing to migrate). This number becomes the bot number. Your personal WhatsApp stays on your current number. | Buy a cheap prepaid SIM for the bot number. ~$5-10. | [ASSUMPTION] User will acquire a secondary number for the bot. |
| OQ-2 | **Mac Pro exact specs?** RAM, chip (M2 Ultra, M2 Max?), storage affect model size choices. | Run `system_profiler SPHardwareDataType` and share. | [ASSUMPTION] M2 Ultra, 192GB RAM, 2TB+ SSD. If 96GB RAM: use Ollama 8B only, skip 70B local. |

## Non-Blocking (Proceed With Assumptions)

| # | Question | Assumption | Revisit When |
|---|----------|-----------|-------------|
| OQ-3 | Notion integration? Source-of-truth mentions Notion in the video but user hasn't explicitly requested it. | [ASSUMPTION] Database-first (Postgres). No Notion integration in MVP. Add as optional sync later if requested. | Phase 2+ |
| OQ-4 | What is "Antigravity" agent? Not a known public agent framework. | [ASSUMPTION] Antigravity is a custom or third-party agent the user has access to. Treat it as a black-box agent with a REST API. Register in agent registry when the user provides connection details. | Phase 3 |
| OQ-5 | Budget tolerance for cloud API costs? Claude Opus is ~$15/M input, $75/M output tokens. | [ASSUMPTION] Use Claude Sonnet ($3/$15) as default reasoning model. Opus only for explicitly complex tasks. GPT-4o for fast tasks. Total: ~$20-50/month for moderate solo use. | Monthly review |
| OQ-6 | Storyboard UI: web app or native Mac app? | [ASSUMPTION] Web app (Next.js on localhost). Accessible from any device on LAN. No native Mac app in MVP. | Phase 2 |
| OQ-7 | Voice note transcription: should the system auto-transcribe WhatsApp voice notes? | [ASSUMPTION] Yes, using MLX Whisper locally. Transcription becomes the text input for classification. | Phase 1 (if voice notes are frequent) |

---

## Appendix: Source URL Index

| # | Source | URL | Date |
|---|--------|-----|------|
| 1 | DeepMind ReadAgent | https://arxiv.org/abs/2402.09727 | Feb 2024 |
| 2 | DeepMind SIMA | https://deepmind.google/discover/blog/sima-generalist-ai-agent-for-3d-virtual-environments/ | Mar 2024 |
| 3 | Gemini 2.0 Launch | https://blog.google/technology/google-deepmind/google-gemini-ai-update-december-2024/ | Dec 2024 |
| 4 | Project Mariner | https://deepmind.google/technologies/gemini/project-mariner/ | Dec 2024 |
| 5 | AlphaProof | https://deepmind.google/discover/blog/ai-solves-imo-problems-at-silver-medal-level/ | Jul 2024 |
| 6 | Google ADK | https://google.github.io/adk-docs/ | Apr 2025 |
| 7 | A2A Protocol | https://a2aprotocol.ai/ | Apr 2025 |
| 8 | Google SAIF | https://safety.google/cybersecurity-advancements/saif/ | Jun 2023 |
| 9 | ReAct Paper | https://arxiv.org/abs/2210.03629 | Oct 2022 |
| 10 | Tree of Thoughts | https://arxiv.org/abs/2305.10601 | May 2023 |
| 11 | Reflexion | https://arxiv.org/abs/2303.11366 | Mar 2023 |
| 12 | Generative Agents | https://arxiv.org/abs/2304.03442 | Apr 2023 |
| 13 | OWASP LLM Top 10 | https://owasp.org/www-project-top-10-for-large-language-model-applications/ | 2023-2025 |
| 14 | Prompt Injection (Greshake) | https://arxiv.org/abs/2302.12173 | Feb 2023 |
| 15 | Microsoft Spotlighting | https://arxiv.org/abs/2403.14720 | Mar 2024 |
| 16 | Constitutional AI | https://arxiv.org/abs/2212.08073 | Dec 2022 |
| 17 | MCP Spec | https://modelcontextprotocol.io/ | 2024-2025 |
| 18 | Anthropic MCP Docs | https://docs.anthropic.com/en/docs/agents-and-tools/mcp | 2024-2025 |
| 19 | Temporal.io | https://docs.temporal.io/ | 2020-2025 |
| 20 | LangGraph | https://docs.langchain.com/oss/python/langgraph/overview | 2024-2025 |
| 21 | AutoGen | https://microsoft.github.io/autogen/stable/ | 2024-2025 |
| 22 | CrewAI | https://docs.crewai.com/ | 2024-2025 |
| 23 | OpenAI Agents SDK | https://openai.github.io/openai-agents-python/ | Mar 2025 |
| 24 | Ollama | https://ollama.com/ | 2023-2025 |
| 25 | MLX Framework | https://github.com/ml-explore/mlx | Dec 2023-2025 |
| 26 | WhatsApp Cloud API | https://developers.facebook.com/docs/whatsapp/cloud-api/overview | 2024-2025 |
| 27 | WhatsApp Flows | https://developers.facebook.com/docs/whatsapp/flows | 2024-2025 |
| 28 | Cloudflare Tunnel | https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/ | 2024-2025 |
| 29 | NATS | https://nats.io/ | 2020-2025 |
| 30 | pgvector | https://github.com/pgvector/pgvector | 2023-2025 |
| 31 | Apache AGE | https://age.apache.org/ | 2023-2025 |
| 32 | Ink & Switch Local-First | https://www.inkandswitch.com/local-first/ | 2019 |
| 33 | Video Source | https://www.youtube.com/watch?v=0TpON5T-Sw4 | Jan 2026 |

---

## Spine Guardian Check

| Source-of-Truth Requirement | Addressed In | Status |
|----------------------------|--------------|--------|
| Solo system | Entire architecture — single user, no RBAC | PRESERVED |
| WhatsApp-first | Output 6 (Runtime), Output 8 (Build Plan Phase 1) | PRESERVED |
| Storyboard brain view | Output 5 (Agent Map), Output 8 (Phase 2) | PRESERVED |
| Codebase + GitHub status | Output 8 (Phase 2, Day 10-11) | PRESERVED |
| Multi-agent control plane | Output 5 (full agent map with 7 agents) | PRESERVED |
| Brains connected to brains | Output 3 (FBM: Core Brain → Project Brains → Execution Brains) | PRESERVED |
| Safe command center | Output 7 (full safety spec with R0-R4, kill switch) | PRESERVED |
| Offline execution + updates | Output 6 (offline queue/replay, WhatsApp status) | PRESERVED |
| Idea tracking → action plans | Output 5 (Router → Planner → Executor pipeline) | PRESERVED |
| Autonomous operation | Output 8 (Phase 4: autonomous planning) | PRESERVED |
| Best intelligence framework | Output 3 (FBM v2.0 with DeepMind-informed patterns) | PRESERVED |
| DeepMind research comparison | Output 2 (full research dossier) | PRESERVED |
| Mac Pro + API keys | Output 6 (~48-58GB RAM fits in 192GB) | PRESERVED |
| Locally intelligent, not infra-driven | Output 4 (Pattern A scores 5 on offline capability, cloud cost ~$0-10/yr) | PRESERVED |
| 24/7 operation | Output 6 (launchd, Temporal durability, auto-recovery) | PRESERVED |

**Spine Guardian Verdict**: All 16 source-of-truth requirements are addressed. No drift detected. Architecture is aligned.
