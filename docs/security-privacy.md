# Security and Privacy

## Private vs public

- `/dashboard/*` is private and login-protected
- public pages and public chat use approved public facts only
- private ask-brain, MCP, and API flows are separate

## Secrets

- secrets are encrypted at rest
- secrets do not enter normal retrieval, boards, digests, or public chat
- owner DM is the trusted direct-reveal lane
- dashboard and API secret reveal require step-up verification

## Public chat

- Turnstile
- rate limits
- topic gate
- prompt-injection refusal
- secret/PII scrubber
- no tool use

## Recommended settings

- strong `DASHBOARD_SESSION_SECRET`
- strong `API_TOKEN`
- `DASHBOARD_COOKIE_SECURE=true` in production
- `DISCORD_OWNER_USER_ID` set correctly
- at least one audit path for secret access enabled
