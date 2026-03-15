"""Pydantic schemas for the private brain API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ManualIngestRequest(BaseModel):
    text: str = Field(min_length=1)
    category: str | None = None
    source: str = "manual"
    tags: list[str] = Field(default_factory=list)


class CollectorRepoPayload(BaseModel):
    name: str
    owner: str | None = None
    url: str | None = None
    branch: str | None = None
    local_path: str | None = None
    is_primary: bool = False


class CollectorEntryPayload(BaseModel):
    external_id: str | None = None
    content_hash: str | None = None
    project_ref: str | None = None
    title: str
    body_markdown: str = ""
    summary: str | None = None
    category: str = "note"
    entry_type: str = "context_dump"
    tags: list[str] = Field(default_factory=list)
    source_links: list[str] = Field(default_factory=list)
    external_url: str | None = None
    happened_at: datetime | None = None
    raw_body_markdown: str | None = None
    is_sensitive: bool = False
    metadata: dict = Field(default_factory=dict)
    repo: CollectorRepoPayload | None = None


class CollectorIngestRequest(BaseModel):
    source_type: str = "collector"
    source_name: str = "mac-collector"
    mode: str = "sync"
    device_name: str
    emit_sync_event: bool = True
    entries: list[CollectorEntryPayload] = Field(default_factory=list)


class QueryRequest(BaseModel):
    question: str = Field(min_length=3)
    mode: str | None = None
    category: str | None = None
    use_opus: bool = False
    include_web: bool = True


class ReminderCreateRequest(BaseModel):
    text: str = Field(min_length=3)
    project_name: str | None = None
    discord_channel_id: str | None = None


class ProjectStateRefreshRequest(BaseModel):
    project_ids: list[str] = Field(default_factory=list)


class ProjectManualStateRequest(BaseModel):
    project_name: str = Field(min_length=1)
    manual_state: str = Field(min_length=1)


class SyncReportRequest(BaseModel):
    source_type: str
    source_name: str
    mode: str = "sync"
    status: str
    items_seen: int = 0
    items_imported: int = 0
    device_name: str | None = None
    error: str | None = None
    metadata: dict = Field(default_factory=dict)


class SyncRunResponse(BaseModel):
    status: str
    sync_run_id: str | None = None
    items_seen: int | None = None
    items_imported: int | None = None
    reason: str | None = None


class AgentSessionBootstrapRequest(BaseModel):
    agent_kind: str
    session_id: str
    cwd: str | None = None
    project_hint: str | None = None
    task_hint: str | None = None
    include_web: bool = True


class AgentSessionCloseoutRequest(BaseModel):
    agent_kind: str
    session_id: str
    cwd: str | None = None
    project_ref: str | None = None
    summary: str = Field(min_length=3)
    decisions: list[str] = Field(default_factory=list)
    changes: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    source_links: list[str] = Field(default_factory=list)
    transcript_excerpt: str | None = None


class ArtifactModerationRequest(BaseModel):
    category: str | None = None
    capture_intent: str | None = None
    validation_status: str | None = None
    quality_issues: list[dict] = Field(default_factory=list)
    eligible_for_boards: bool | None = None
    eligible_for_project_state: bool | None = None
    moderation_notes: str | None = None
    resolved_by: str | None = None


class BoardRegenerateRequest(BaseModel):
    board_type: str = Field(min_length=1)
    target_date: str = Field(min_length=8)


class EvalRunRequest(BaseModel):
    run_name: str = Field(default="retrieval-reliability", min_length=3)
    rounds: int = Field(default=3, ge=1, le=10)
