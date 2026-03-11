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
    metadata: dict = Field(default_factory=dict)
    repo: CollectorRepoPayload | None = None


class CollectorIngestRequest(BaseModel):
    source_name: str = "mac-collector"
    mode: str = "sync"
    device_name: str
    entries: list[CollectorEntryPayload] = Field(default_factory=list)


class SyncRunResponse(BaseModel):
    status: str
    sync_run_id: str | None = None
    items_seen: int | None = None
    items_imported: int | None = None
    reason: str | None = None
