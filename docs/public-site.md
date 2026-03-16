# Public Site

The public surface is intentionally separate from the private brain.

## Routes

- `/`
- `/about`
- `/projects`
- `/projects/{slug}`
- `/open-brain`

## Data model

Public pages and public chat read only from:

- `PublicFactRecord`
- `PublicProfileSnapshot`
- `PublicProjectSnapshot`
- `PublicFAQSnapshot`
- `PublicAnswerPolicy`

Nothing becomes public automatically. Facts must be approved first.

## Dynamic behavior

- approved public facts are the allowlist layer
- public snapshots rebuild from that allowlist
- selected projects can also pull live public-safe status from current project-state snapshots
- public chat retrieves relevant approved facts for each question before composing an answer

## Seeding

Use approved markdown in `PUBLIC_PROFILE_SEED_PATH`, then run:

```bash
./.venv/bin/python scripts/refresh_public_surface.py
```

## Safety

- public chat is topic-gated
- Turnstile verification is mandatory when configured
- rate limiting is enforced server-side
- outputs are scrubbed for secrets and sensitive numeric patterns
- public chat never uses private-brain retrieval or secret tools
