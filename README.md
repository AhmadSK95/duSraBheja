# duSraBheja

An open-source Brain OS for Discord intake, private memory, agent bootstrapping, and a public profile layer.

## What it does

- captures text, images, audio, links, and planner pages from Discord
- promotes raw evidence into canonical memory:
  - `evidence`
  - `observations`
  - `episodes`
  - `threads`
  - `entities`
  - `syntheses`
- exposes the brain to Codex, Claude Code, and other agents through MCP, HTTP, and CLI workflows
- serves a private dashboard for library inspection, project state, public-fact approval, and secret-vault access
- serves a public profile site and public chatbot from approved public facts only

## Core ideas

- story is presentation, not storage
- public answers come from an allowlist layer, not from the private archive
- secrets are encrypted and isolated from normal retrieval
- the system is always-on and machine-native, not a simulation of human forgetting

## Quickstart

1. Copy the example config:

```bash
cp .env.example .env
cp providers.example.yaml providers.yaml
```

2. Install dependencies:

```bash
uv sync --extra dev
```

3. Run services locally:

```bash
uv run alembic upgrade head
uv run python -m src.api.app
uv run python -m src.bot.main
uv run python -m src.worker.main
uv run python -m src.mcp.server
```

4. Open the private dashboard:

- `/dashboard/login`

5. Seed the public surface if you have public-safe markdown ready:

```bash
./.venv/bin/python scripts/refresh_public_surface.py
```

## Public / Private split

- Public:
  - `/`
  - `/about`
  - `/projects`
  - `/open-brain`
- Private:
  - `/dashboard/*`
  - `/api/*`
  - MCP / CLI session workflows

Public pages and the public chatbot read from `PublicFactRecord` and derived public snapshots only.

## Agent usage

Use the brain before work and publish back into it after work:

```bash
./.venv/bin/python scripts/brain_session.py bootstrap --agent-kind codex --project-hint duSraBheja
./.venv/bin/python scripts/brain_session.py closeout --agent-kind codex --session-id <id> --project-ref duSraBheja --summary "what changed"
```

See:

- [Agent Brain Workflow](docs/agent-brain-workflow.md)
- [MCP Integration](docs/mcp-integration.md)

## Documentation

- [Self Hosting](docs/self-hosting.md)
- [Providers](docs/providers.md)
- [Public Site](docs/public-site.md)
- [Security and Privacy](docs/security-privacy.md)
- [Agent Brain Workflow](docs/agent-brain-workflow.md)
- [MCP Integration](docs/mcp-integration.md)

## License

Apache-2.0. See [LICENSE](LICENSE).
