# Agent Brain Workflow

This repo now has a concrete session loop for Codex and Claude, plus a one-time import path for the personal context that lives outside Discord.

## Session Start

Run this before working so the agent reboots from the brain instead of from scratch:

```bash
./.venv/bin/python scripts/brain_session.py bootstrap \
  --agent-kind codex \
  --project-hint duSraBheja
```

Useful flags:

- `--task-hint "ship the digest repair"` to bias the reboot brief toward the current task.
- `--no-include-web` to keep the reboot fully local.
- `--format json` if you want to feed the payload into another script or wrapper.

Claude uses the same flow:

```bash
./.venv/bin/python scripts/brain_session.py bootstrap \
  --agent-kind claude \
  --project-hint duSraBheja
```

## During The Session

The MCP tools are the shared working surface:

- `bootstrap_session`
- `get_project_context`
- `publish_progress`
- `query_brain_mode`
- `publish_session_closeout`

If the agent already has MCP connected, it should use those tools instead of rebuilding context from raw files each time.

## Session End

Publish a structured closeout so the next session starts from the last saved point:

```bash
./.venv/bin/python scripts/brain_session.py closeout \
  --agent-kind codex \
  --session-id codex-1234abcd \
  --project-ref duSraBheja \
  --summary "Tightened identity resolution and added the life import pipeline." \
  --change "Added one-time Gmail/Drive/Keep/history importer" \
  --change "Added Discord bot-post reset tooling"
```

The closeout sits alongside the transcript backfill from `run_agent_history_sync.sh`, but it is much cleaner for project-state and digest generation because it is already structured.

## One-Time Personal Context Import

Use Google Takeout plus any OTT exports you have locally. This flow prepares the data on the Mac, then ships it to the droplet through the private ingest API.

1. Export Google Takeout with at least:
   Gmail, Drive, Keep, and My Activity for Search plus YouTube and YouTube Music.
2. Put OTT exports in a local folder. CSV and JSON work best.
3. Run:

```bash
./scripts/run_life_import.sh bootstrap \
  --takeout-root ~/Downloads/Takeout \
  --ott-root ~/Downloads/ott-exports
```

What it imports:

- Apple Notes via the existing Notes exporter, unless you add `--skip-apple-notes`
- Gmail `.mbox` mailboxes
- Drive export files
- Keep notes
- YouTube history
- Google search history
- OTT watch-history exports

All of these sources are marked sensitive and go through the protected-content path on ingest.

## Reset Discord Bot Posts

Preview what would be deleted:

```bash
./.venv/bin/python scripts/reset_bot_posts.py
```

Actually delete bot-authored posts in the configured brain channels:

```bash
./.venv/bin/python scripts/reset_bot_posts.py --execute
```

You can narrow it with `--channel daily-digest` or broaden it with `--all-text-channels`.
