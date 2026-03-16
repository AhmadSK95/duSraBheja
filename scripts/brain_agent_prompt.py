#!/usr/bin/env python3
"""Print a copy-paste prompt that tells an AI agent how to use the shared brain."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
BRAIN_SESSION = ROOT / "scripts" / "brain_session.py"


def build_prompt(*, agent_kind: str, project_hint: str | None, task_hint: str | None, cwd: str | None) -> str:
    current_dir = cwd or os.getcwd()
    bootstrap = (
        f"{VENV_PYTHON} {BRAIN_SESSION} bootstrap "
        f"--agent-kind {agent_kind} "
        f'--cwd "{current_dir}"'
    )
    if project_hint:
        bootstrap += f' --project-hint "{project_hint}"'
    if task_hint:
        bootstrap += f' --task-hint "{task_hint}"'

    closeout = (
        f"{VENV_PYTHON} {BRAIN_SESSION} closeout "
        f"--agent-kind {agent_kind} "
        f"--session-id <session-id> "
        f'--cwd "{current_dir}"'
    )
    if project_hint:
        closeout += f' --project-ref "{project_hint}"'
    closeout += ' --summary "<what changed>"'

    story = (
        f"{VENV_PYTHON} {BRAIN_SESSION} story "
        f"--agent-kind {agent_kind} "
        f"--session-id <session-id> "
    )
    if project_hint:
        story += f'--project-ref "{project_hint}" '
    story += '--title "<direction update title>" --summary "<direction update>"'

    return f"""Use Ahmad's shared brain before doing any work.

1. Bootstrap from the brain:
```bash
{bootstrap}
```

2. Treat the reboot brief as the current project context before you inspect code.

3. During the session, if MCP is available, prefer these tools:
- `bootstrap_session`
- `query_brain_mode`
- `publish_curated_session_story`
- `publish_session_closeout`

4. If this session changes architecture, product direction, or project understanding, publish a curated story:
```bash
{story}
```

5. At the end of the session, publish a closeout:
```bash
{closeout}
```
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Print a copy-paste brain handoff prompt for Codex or Claude.")
    parser.add_argument("--agent-kind", choices=("codex", "claude"), default="codex")
    parser.add_argument("--project-hint")
    parser.add_argument("--task-hint")
    parser.add_argument("--cwd")
    args = parser.parse_args()
    print(
        build_prompt(
            agent_kind=args.agent_kind,
            project_hint=args.project_hint,
            task_hint=args.task_hint,
            cwd=args.cwd,
        ).strip()
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
