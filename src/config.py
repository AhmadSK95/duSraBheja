"""Central configuration — reads from .env via pydantic-settings."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://brain_user:changeme@localhost:5432/brain_db"
    db_pool_size: int = 5
    db_max_overflow: int = 5
    db_statement_timeout_ms: int = 30_000
    db_ssl: bool = False
    db_ssl_require: bool = False
    db_ssl_reject_unauthorized: bool = True

    # Redis
    redis_url: str = "redis://localhost:6379"

    # Discord
    discord_token: str = ""
    discord_guild_id: int = 0
    inbox_channel_name: str = "inbox"
    needs_review_channel_name: str = "needs-review"
    daily_board_channel_name: str = "daily-board"
    weekly_board_channel_name: str = "weekly-board"
    daily_digest_channel_name: str = "daily-digest"
    ask_channel_name: str = "ask-brain"
    brain_voice_instructions: str = (
        "Write like Ahmad: direct, thoughtful, low-fluff, builder-operator energy."
    )

    # Anthropic (Claude)
    anthropic_api_key: str = ""
    classifier_model: str = "claude-haiku-4-5-20251001"
    sonnet_model: str = "claude-sonnet-4-6"
    opus_model: str = "claude-opus-4-6"

    # OpenAI (embeddings + Whisper)
    openai_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    whisper_model: str = "whisper-1"
    openai_web_search_model: str = "gpt-4.1"

    # Classification
    confidence_threshold: float = 0.75
    max_clarification_attempts: int = 2

    # Chunking
    chunk_max_tokens: int = 512
    chunk_overlap_tokens: int = 64

    # MCP
    mcp_transport: str = "streamable-http"
    mcp_port: int = 8100

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_token: str = ""
    app_base_url: str = "http://127.0.0.1:8000"

    # Blob storage
    blob_storage_path: str = "/data/blobs"
    encryption_master_key: str = ""

    # Daily digest
    digest_cron_hour: int = 8
    digest_timezone: str = "America/New_York"
    digest_story_pulse_cooldown_minutes: int = 15
    weekly_board_cron_weekday: int = 0
    knowledge_refresh_hours: int = 6
    knowledge_max_projects_per_run: int = 3
    cognition_refresh_hours: int = 4
    voice_refresh_hour: int = 5
    startup_replay_enabled: bool = True
    startup_replay_history_limit: int = 0
    startup_replay_author_ids: str = ""
    startup_replay_channel_names: str = "inbox"

    # Collector
    collector_device_name: str = "macbook"
    collector_interval_hours: int = 4
    collector_project_roots: str = ""
    collector_bootstrap_roots: str = ""
    collector_daily_roots: str = ""
    collector_state_path: str = "~/.brain-collector/state.json"
    collector_api_base_url: str = "http://127.0.0.1:8000"
    collector_scan_max_depth: int = 4
    collector_inventory_recent_files_limit: int = 50
    agent_history_state_path: str = "~/.brain-collector/agent-history-state.json"
    agent_history_idle_seconds: int = 300
    agent_history_poll_seconds: int = 900
    apple_notes_export_path: str = "~/.brain-collector/apple-notes"
    apple_notes_state_path: str = "~/.brain-collector/apple-notes-state.json"
    apple_notes_exclude_folders: str = ""
    browser_activity_state_path: str = "~/.brain-collector/browser-activity-state.json"
    browser_activity_lookback_days: int = 1

    # Story retrieval
    story_max_events: int = 25

    # GitHub
    github_api_token: str = ""
    github_api_base_url: str = "https://api.github.com"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()
