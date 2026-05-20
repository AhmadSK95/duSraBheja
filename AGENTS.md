# AGENTS.md

The conventions, architecture, commands, and rules for this repository live in [CLAUDE.md](CLAUDE.md). They apply to every agent working in the codebase — Claude Code, Codex, and otherwise.

Read CLAUDE.md before doing any work. It is the single source of truth for:

- Commands (install, run, lint, test, migrate, docker)
- Architecture (Discord → bot → ARQ worker → librarian pipeline)
- Key layers (agents, services, worker tasks, API routes, MCP tools, bot cogs, collector, lib, core modules)
- Code patterns (database sessions, agent base layer, LLM calls, worker tasks, MCP tool registration, API auth)
- Memory model (evidence → observations → episodes → threads → entities → syntheses)
- Public / private split and secret-vault rules
- LLM model routing and the 9 canonical categories
- Key rules (async everywhere, bot enqueues / worker processes, separate DB, cost tracking, 0.75 confidence threshold)
- Agent session loop (bootstrap + closeout)

If you find yourself about to document a project convention here, put it in CLAUDE.md instead and let this file stay as a pointer.
