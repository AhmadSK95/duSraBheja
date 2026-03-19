# Improved Project Descriptions — Resume + LinkedIn
> For Claude Cowork to reference when updating LinkedIn and resume
> Created: March 16, 2026

---

## PROFESSIONAL SUMMARY (Resume)

Software Engineer with 6+ years building distributed systems at Amazon and enterprise loyalty platforms. At Amazon, built advertising services that powered international marketplace expansion - Java and Python microservices handling ad delivery across multiple AWS regions at 99.9%+ availability. Currently building two AI-native products: duSraBheja, a personal AI second brain where anything I capture (text, images, audio, PDFs) gets automatically classified, embedded, and made searchable by AI agents through MCP - solving the problem of scattered knowledge across tools. And dataGenie, a conversational data analytics platform that lets non-technical users explore datasets in plain English without writing SQL. IIT Kharagpur and NYU Tandon grad. I work across the full stack and I like owning a product end to end.

---

## KEY PROJECTS

### dataGenie - Multi-Agent Data Analytics Platform

**Resume (3 bullets):**

- Non-technical users shouldn't need to write SQL to explore their own data. dataGenie lets you upload a CSV, ask questions in plain English, and get back query results, charts, and written explanations - making data analysis as easy as having a conversation.
- Designed a hybrid query architecture that routes simple questions directly to SQL and sends complex, multi-step questions through an agentic ReAct loop that decomposes them into sub-tasks. This was the hardest design decision - a single path either over-engineered simple queries or couldn't handle complex ones.
- Built the full backend: FastAPI with DuckDB for fast analytical queries, a multi-provider LLM layer with automatic fallback (Claude > OpenAI > Ollama), async task processing via Celery/Redis, and a profiling engine that auto-generates column-level data quality scores before any query runs. Tech: Python, FastAPI, DuckDB, Redis, Docker.

**LinkedIn (longer form):**

dataGenie started from a real frustration: every time a non-technical teammate needed data from a CSV, they'd either ask an engineer to write a query or struggle with Excel pivot tables. I wanted to build something where you could just ask "what were the top 5 products by revenue last quarter?" and get back a proper answer with a chart.

The interesting engineering challenge was query routing. Simple questions ("how many rows?") don't need an agent - they map directly to SQL. But complex questions ("compare Q3 vs Q4 trends, break down by region, and explain what changed") need to be decomposed into sub-tasks. I built a hybrid architecture: an intent classifier decides the path, and complex queries go through a ReAct loop that plans, executes, and synthesizes across multiple SQL calls.

The LLM layer supports Claude, OpenAI, and Ollama with automatic fallback chains - so if one provider is down or rate-limited, queries still work. Before any question is answered, a profiling engine auto-generates column-level stats (nulls, distributions, cardinality, data types) so the LLM has context about the data shape.

What I learned: Building agentic systems is less about the LLM and more about the orchestration - error recovery, sub-task dependencies, knowing when to stop decomposing. Also learned a lot about DuckDB's analytical query engine vs traditional OLTP databases.

Stack: Python, FastAPI, DuckDB, Redis, Celery, Docker. Frontend planned in Next.js.

**What this project demonstrates:**
- Can design multi-agent/agentic architectures from scratch
- Understands LLM integration patterns (fallback chains, prompt routing, structured output)
- Full-stack backend ownership (API design, async processing, data pipelines)
- Product thinking - started from a user problem, not a tech stack

---

### duSraBheja - Personal AI Second Brain & MCP Server

**Resume (3 bullets):**

- I capture ideas, notes, and references constantly - across Discord, conversations, PDFs, voice memos - but they end up scattered and unfindable. duSraBheja solves this: 5 specialized AI agents (classifier, librarian, retriever, clarifier, storyteller) automatically categorize, embed, and merge everything into canonical knowledge notes with semantic search.
- Built an MCP server (FastMCP) that exposes the brain as 6 tools so any MCP-compatible AI agent (Claude Code, Codex) can read from and write to my knowledge base. This turns a personal note system into an AI-accessible memory layer - when I'm coding, the AI can pull relevant context without me copy-pasting anything.
- Stack: Python, discord.py, FastAPI, PostgreSQL + pgvector for vector search, ARQ (Redis-backed async job queue), SQLAlchemy 2.0, Alembic, Docker Compose. Deployed on DigitalOcean with separate services for bot, worker, MCP server, and Redis.

**LinkedIn (longer form):**

The problem: I have notes in Discord, bookmarks in the browser, PDFs on my desktop, voice memos on my phone, ideas in random text files. When I need to find something I captured three weeks ago, I can't. Every knowledge management tool I tried required manual organization - and I don't do manual organization consistently.

duSraBheja is my answer: drop anything into a Discord channel, and 5 specialized AI agents handle the rest. A classifier (Claude Haiku - fast and cheap) categorizes it with structured JSON output including confidence scores. A librarian (Claude Sonnet) checks if this relates to something already in the brain and merges notes intelligently. Everything gets embedded (OpenAI text-embedding-3-small) and stored in PostgreSQL with pgvector for semantic search.

The MCP server is what makes this more than a note app. By exposing the brain as 6 tools through FastMCP, any MCP-compatible AI (Claude Code, Codex, etc.) can search, capture, and retrieve from my knowledge base mid-conversation. When I'm working on dataGenie in Claude Code, the AI can pull my prior architecture decisions, notes from debugging sessions, or design ideas - without me having to find and paste them.

Architecture decisions I'm proud of: Bot enqueues jobs but never blocks on LLM calls (ARQ handles async processing). Every LLM call logs model, tokens, and cost to an audit log for cost tracking. Confidence threshold at 0.75 routes low-confidence classifications to a review flow instead of silently miscategorizing.

What I learned: Prompt engineering for structured output is an art - getting Claude to consistently return valid JSON with confidence scores took significant iteration. Also learned async Python deeply (asyncpg, ARQ, SQLAlchemy 2.0 async) and operational patterns for running multiple services on a single droplet.

Stack: Python 3.12, discord.py, FastAPI, PostgreSQL 16 + pgvector, ARQ, SQLAlchemy 2.0, Alembic, Docker Compose on DigitalOcean.

**What this project demonstrates:**
- Multi-agent system design (specialized agents with different models for different tasks)
- MCP protocol implementation - making a system AI-interoperable
- Production deployment and operations (Docker Compose, DigitalOcean, separate services)
- Async Python mastery (asyncpg, ARQ, async SQLAlchemy)
- Cost-conscious AI engineering (model routing by task complexity)

---

### Kaffa Espresso Bar Website - Live Client Project

**Resume (1 bullet):**

- Freelance project for a Jersey City coffee shop - handled the full lifecycle from client meetings through deployment. Single-page site with full-bleed photography, menu, and Instagram integration. Set up custom domain, HTTPS via certbot, and SEO. The site is the shop's primary online presence and live at kaffaespressobar.com.

**LinkedIn:**

Freelance web project for Kaffa Espresso Bar in Jersey City. Managed the entire lifecycle: client meetings to understand their brand, design, development, domain setup, HTTPS via certbot, SEO configuration, and deployment. The site serves as the shop's primary online presence.

What this demonstrates: I can work directly with non-technical clients, manage a project end-to-end, and ship something real that a business depends on.

---

### Balkan Barbershop Website - Live Client Project

**Resume (1 bullet):**

- Second freelance client - built a portrait-driven editorial layout with Framer Motion animations that matched the barbershop's aesthetic. Managed design, development, and deployment. Live at balkan.thisisrikisart.com.

**LinkedIn:**

Production website for a local barbershop in Jersey City. Designed a portrait-driven editorial layout with Framer Motion animations, team portraits, and neighborhood backgrounds. Managed the full project lifecycle from design through deployment and ongoing maintenance.

What this demonstrates: Frontend design sense, Framer Motion animation skills, and ability to deliver polished client-facing work.

---

## SUMMARY: What These Projects Together Demonstrate

| Capability | Evidence |
|---|---|
| Multi-agent / agentic AI systems | duSraBheja (5 agents), dataGenie (ReAct loop) |
| LLM integration at depth | Model routing, fallback chains, structured output, cost tracking |
| MCP protocol | Built an MCP server from scratch with FastMCP |
| Full-stack backend | FastAPI, DuckDB, PostgreSQL, Redis, Celery, Docker |
| Production deployment | DigitalOcean, Docker Compose, certbot, domain management |
| Async Python | asyncpg, ARQ, SQLAlchemy 2.0 async |
| Product thinking | Both AI projects started from real user problems |
| Client-facing delivery | Two live freelance websites |
| End-to-end ownership | Every project is solo, start to finish |
