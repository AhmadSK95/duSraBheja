"""SQLAlchemy ORM models for brain storage and story tracking."""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class Artifact(Base):
    __tablename__ = "artifacts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    discord_message_id = Column(String, unique=True, nullable=True)
    discord_channel_id = Column(String, nullable=True)
    discord_thread_id = Column(String, nullable=True)
    content_type = Column(String, nullable=False)
    raw_text = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    blob_ref = Column(String, nullable=True)
    blob_hash = Column(String, nullable=True)
    blob_mime = Column(String, nullable=True)
    blob_size_bytes = Column(Integer, nullable=True)
    metadata_ = Column("metadata", JSONB, default=dict)
    source = Column(String, nullable=False, default="discord")
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    classifications = relationship("Classification", back_populates="artifact", cascade="all, delete")
    chunks = relationship("Chunk", back_populates="artifact", cascade="all, delete")
    reviews = relationship("ReviewQueue", back_populates="artifact", cascade="all, delete")
    journal_entries = relationship("JournalEntry", back_populates="artifact")
    source_items = relationship("SourceItem", back_populates="artifact")

    __table_args__ = (
        Index("idx_artifacts_created", "created_at"),
        Index("idx_artifacts_content_type", "content_type"),
        Index("idx_artifacts_source", "source"),
    )


class Classification(Base):
    __tablename__ = "classifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    artifact_id = Column(UUID(as_uuid=True), ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False)
    category = Column(String, nullable=False)
    confidence = Column(Float, nullable=False)
    entities = Column(JSONB, default=list)
    tags = Column(ARRAY(String), default=list)
    priority = Column(String, default="medium")
    suggested_action = Column(Text, nullable=True)
    model_used = Column(String, nullable=False)
    tokens_used = Column(Integer, nullable=True)
    cost_usd = Column(Numeric(10, 6), nullable=True)
    is_final = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    artifact = relationship("Artifact", back_populates="classifications")

    __table_args__ = (
        Index("idx_class_artifact", "artifact_id"),
        Index("idx_class_category", "category"),
        Index("idx_class_confidence", "confidence"),
    )


class Note(Base):
    __tablename__ = "notes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    category = Column(String, nullable=False)
    title = Column(String, nullable=False)
    content = Column(Text, nullable=True)
    status = Column(String, nullable=False, default="active")
    priority = Column(String, default="medium")
    tags = Column(ARRAY(String), default=list)
    due_date = Column(DateTime(timezone=True), nullable=True)
    remind_at = Column(DateTime(timezone=True), nullable=True)
    metadata_ = Column("metadata", JSONB, default=dict)
    discord_channel_id = Column(String, nullable=True)
    discord_message_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    chunks = relationship("Chunk", back_populates="note")
    journal_entries = relationship("JournalEntry", back_populates="project_note")
    source_items = relationship("SourceItem", back_populates="project_note")
    repos = relationship("ProjectRepo", back_populates="project_note")

    __table_args__ = (
        Index("idx_notes_category", "category"),
        Index("idx_notes_status", "status"),
        Index("idx_notes_tags", "tags", postgresql_using="gin"),
    )


class Chunk(Base):
    __tablename__ = "chunks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    artifact_id = Column(UUID(as_uuid=True), ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False)
    note_id = Column(UUID(as_uuid=True), ForeignKey("notes.id", ondelete="SET NULL"), nullable=True)
    chunk_index = Column(Integer, nullable=False, default=0)
    content = Column(Text, nullable=False)
    token_count = Column(Integer, nullable=True)
    embedding = Column(Vector(1536), nullable=True)
    metadata_ = Column("metadata", JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    artifact = relationship("Artifact", back_populates="chunks")
    note = relationship("Note", back_populates="chunks")

    __table_args__ = (
        Index("idx_chunks_artifact", "artifact_id"),
        Index("idx_chunks_note", "note_id"),
    )


class Link(Base):
    __tablename__ = "links"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_type = Column(String, nullable=False)
    source_id = Column(UUID(as_uuid=True), nullable=False)
    target_type = Column(String, nullable=False)
    target_id = Column(UUID(as_uuid=True), nullable=False)
    relation = Column(String, nullable=False)
    confidence = Column(Float, default=1.0)
    metadata_ = Column("metadata", JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("source_type", "source_id", "target_type", "target_id", "relation"),
        Index("idx_links_source", "source_type", "source_id"),
        Index("idx_links_target", "target_type", "target_id"),
    )


class ReviewQueue(Base):
    __tablename__ = "review_queue"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    artifact_id = Column(UUID(as_uuid=True), ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False)
    classification_id = Column(UUID(as_uuid=True), ForeignKey("classifications.id"), nullable=True)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=True)
    discord_thread_id = Column(String, nullable=True)
    status = Column(String, nullable=False, default="pending")
    attempts = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    answered_at = Column(DateTime(timezone=True), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    artifact = relationship("Artifact", back_populates="reviews")

    __table_args__ = (
        Index("idx_review_status", "status"),
        Index("idx_review_artifact", "artifact_id"),
    )


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    trace_id = Column(UUID(as_uuid=True), nullable=False, default=uuid.uuid4)
    agent = Column(String, nullable=False)
    action = Column(String, nullable=False)
    model_used = Column(String, nullable=True)
    input_summary = Column(Text, nullable=True)
    output_summary = Column(Text, nullable=True)
    tokens_used = Column(Integer, nullable=True)
    cost_usd = Column(Numeric(10, 6), nullable=True)
    duration_ms = Column(Integer, nullable=True)
    error = Column(Text, nullable=True)
    metadata_ = Column("metadata", JSONB, default=dict)

    __table_args__ = (
        Index("idx_audit_timestamp", "timestamp"),
        Index("idx_audit_agent", "agent"),
        Index("idx_audit_trace", "trace_id"),
    )


class Digest(Base):
    __tablename__ = "digests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    digest_date = Column(Date, nullable=False, unique=True)
    payload = Column(JSONB, nullable=False)
    discord_message_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    __table_args__ = (Index("idx_digests_date", "digest_date"),)


class JournalEntry(Base):
    __tablename__ = "journal_entries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    artifact_id = Column(UUID(as_uuid=True), ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True)
    project_note_id = Column(UUID(as_uuid=True), ForeignKey("notes.id", ondelete="SET NULL"), nullable=True)
    source_item_id = Column(UUID(as_uuid=True), ForeignKey("source_items.id", ondelete="SET NULL"), nullable=True)
    subject_type = Column(String, nullable=False, default="topic")
    subject_ref = Column(String, nullable=True)
    entry_type = Column(String, nullable=False, default="note")
    actor_type = Column(String, nullable=False, default="human")
    actor_name = Column(String, nullable=False, default="unknown")
    title = Column(String, nullable=False)
    body_markdown = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    decision = Column(Text, nullable=True)
    rationale = Column(Text, nullable=True)
    constraint = Column(Text, nullable=True)
    outcome = Column(Text, nullable=True)
    impact = Column(Text, nullable=True)
    open_question = Column(Text, nullable=True)
    evidence_refs = Column(JSONB, default=list)
    tags = Column(ARRAY(String), default=list)
    source_links = Column(JSONB, default=list)
    metadata_ = Column("metadata", JSONB, default=dict)
    happened_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    artifact = relationship("Artifact", back_populates="journal_entries")
    project_note = relationship("Note", back_populates="journal_entries")
    source_item = relationship("SourceItem", back_populates="journal_entries")

    __table_args__ = (
        Index("idx_journal_project", "project_note_id"),
        Index("idx_journal_happened", "happened_at"),
        Index("idx_journal_entry_type", "entry_type"),
        Index("idx_journal_subject", "subject_type", "subject_ref"),
    )


class SyncSource(Base):
    __tablename__ = "sync_sources"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_type = Column(String, nullable=False)
    name = Column(String, nullable=False)
    status = Column(String, nullable=False, default="active")
    config = Column(JSONB, default=dict)
    last_synced_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    runs = relationship("SyncRun", back_populates="sync_source", cascade="all, delete")
    items = relationship("SourceItem", back_populates="sync_source", cascade="all, delete")

    __table_args__ = (
        UniqueConstraint("source_type", "name"),
        Index("idx_sync_sources_type", "source_type"),
    )


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sync_source_id = Column(UUID(as_uuid=True), ForeignKey("sync_sources.id", ondelete="CASCADE"), nullable=False)
    mode = Column(String, nullable=False, default="sync")
    status = Column(String, nullable=False, default="running")
    started_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    items_seen = Column(Integer, nullable=False, default=0)
    items_imported = Column(Integer, nullable=False, default=0)
    error = Column(Text, nullable=True)
    metadata_ = Column("metadata", JSONB, default=dict)

    sync_source = relationship("SyncSource", back_populates="runs")

    __table_args__ = (
        Index("idx_sync_runs_source", "sync_source_id"),
        Index("idx_sync_runs_status", "status"),
    )


class SourceItem(Base):
    __tablename__ = "source_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sync_source_id = Column(UUID(as_uuid=True), ForeignKey("sync_sources.id", ondelete="CASCADE"), nullable=False)
    project_note_id = Column(UUID(as_uuid=True), ForeignKey("notes.id", ondelete="SET NULL"), nullable=True)
    artifact_id = Column(UUID(as_uuid=True), ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True)
    external_id = Column(String, nullable=False)
    external_url = Column(String, nullable=True)
    title = Column(String, nullable=False)
    summary = Column(Text, nullable=True)
    payload = Column(JSONB, default=dict)
    content_hash = Column(String, nullable=True)
    happened_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    sync_source = relationship("SyncSource", back_populates="items")
    artifact = relationship("Artifact", back_populates="source_items")
    project_note = relationship("Note", back_populates="source_items")
    journal_entries = relationship("JournalEntry", back_populates="source_item")

    __table_args__ = (
        UniqueConstraint("sync_source_id", "external_id"),
        Index("idx_source_items_project", "project_note_id"),
        Index("idx_source_items_hash", "content_hash"),
    )


class ProjectRepo(Base):
    __tablename__ = "project_repos"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_note_id = Column(UUID(as_uuid=True), ForeignKey("notes.id", ondelete="CASCADE"), nullable=False)
    repo_owner = Column(String, nullable=True)
    repo_name = Column(String, nullable=False)
    repo_url = Column(String, nullable=True)
    branch = Column(String, nullable=True)
    local_path = Column(String, nullable=True)
    is_primary = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    project_note = relationship("Note", back_populates="repos")

    __table_args__ = (
        UniqueConstraint("project_note_id", "repo_name"),
        Index("idx_project_repos_note", "project_note_id"),
    )


class OAuthCredential(Base):
    __tablename__ = "oauth_credentials"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider = Column(String, nullable=False)
    account_email = Column(String, nullable=True)
    scopes = Column(ARRAY(String), default=list)
    encrypted_refresh_token = Column(Text, nullable=True)
    encrypted_access_token = Column(Text, nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    metadata_ = Column("metadata", JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    __table_args__ = (Index("idx_oauth_provider", "provider"),)
