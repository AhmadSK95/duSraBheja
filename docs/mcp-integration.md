# MCP Integration

The brain exposes a private MCP surface for agent sessions.

## Core tools

- `describe_brain_protocol`
- `bootstrap_session`
- `query_library`
- `publish_progress`
- `publish_session_closeout`
- `request_secret_access`
- `reveal_secret_once`

## Recommended agent loop

1. bootstrap from the brain
2. query context as needed
3. publish progress during meaningful work
4. publish a closeout at the end

## Claude Code prompt

```text
Use the duSraBheja Brain MCP server first.
1. Call describe_brain_protocol.
2. Call bootstrap_session with agent_kind="claude", session_id="<unique-id>", cwd="<current-working-directory>", and project_hint="<project>".
3. Use query_library for context and publish_progress for important changes.
4. End with publish_session_closeout.
```

## CLI fallback

```bash
./.venv/bin/python scripts/brain_session.py bootstrap --agent-kind claude --project-hint <project>
./.venv/bin/python scripts/brain_session.py closeout --agent-kind claude --session-id <id> --project-ref <project> --summary "<what changed>"
```
