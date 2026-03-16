# Providers

duSraBheja uses a role-based provider registry.

## Why

Different model roles have different requirements:

- `classifier`
- `reasoning`
- `merge`
- `embed`
- `transcribe`
- `public_chat`
- `web_research`

You can bind all roles to one provider or split them across cloud and local providers.

## Config files

- `.env` stores secrets and base URLs
- `providers.yaml` stores provider topology
- `providers.example.yaml` shows the shape

## Supported kinds

- `anthropic`
- `openai`
- `openai_compatible`
- `ollama`

## Example

```yaml
providers:
  - name: anthropic
    kind: anthropic
    api_key_env: ANTHROPIC_API_KEY
roles:
  - role: reasoning
    provider: anthropic
    model: claude-sonnet-4-6
```

If no `providers.yaml` exists, the app falls back to sensible defaults from `.env`.
