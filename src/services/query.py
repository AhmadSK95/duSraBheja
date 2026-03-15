"""Story-aware query service with traces, exact fact lookup, and project-first ranking."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.storyteller import (
    narrate_exact_fact_answer,
    narrate_status_answer,
    narrate_timeline_answer,
)
from src.config import settings
from src.constants import QUERY_MODES
from src.lib import store
from src.lib.embeddings import embed_text
from src.lib.provenance import (
    DERIVED_ENTRY_TYPES,
    DIRECT_AGENT_ENTRY_TYPES,
    signal_kind_for_artifact,
    signal_kind_for_event,
)
from src.lib.time import describe_event_time, format_display_datetime
from src.services.identity import infer_project_from_text, is_low_signal_project_name, resolve_project
from src.services.openai_web import answer_question_with_web
from src.services.project_state import recompute_project_states
from src.services.story import build_project_story_payload

QUERY_STOPWORDS = {
    "a",
    "about",
    "an",
    "and",
    "for",
    "from",
    "give",
    "how",
    "i",
    "in",
    "is",
    "latest",
    "me",
    "of",
    "on",
    "project",
    "recent",
    "show",
    "since",
    "status",
    "the",
    "timeline",
    "review",
    "best",
    "missing",
    "holes",
    "to",
    "update",
    "updates",
    "what",
    "whats",
    "yesterday",
}

PERSONAL_QUERY_HINTS = (
    "my ",
    "did i ",
    "what did i",
    "which project",
    "what changed on",
    "where did i leave",
    "reminder",
    "notes",
)
ACTIVE_PROJECT_QUERY_HINTS = (
    "active projects",
    "currently active projects",
    "current active projects",
    "what are my active projects",
    "what are ahmad current active projects",
    "which projects am i working on",
    "what am i working on right now",
    "what am i actively working on",
    "which projects are actually active",
    "what projects matter right now",
)
REBOOT_QUERY_HINTS = (
    "where did i leave off",
    "pick up where i left off",
    "bring me up to speed",
    "catch me up on",
    "reboot me on",
)
EXACT_FACT_HINTS = (
    "ip",
    "address",
    "email",
    "url",
    "website",
    "hostname",
    "host",
    "username",
    "login",
    "account",
    "port",
)
QUERY_STAGE_ROUTING = "routing"
QUERY_STAGE_CANDIDATE_RETRIEVAL = "candidate_retrieval"
QUERY_STAGE_NARRATION = "narration"


async def narrate_from_context(
    session: AsyncSession,
    *,
    question: str,
    context_text: str,
    use_opus: bool = False,
    trace_id: uuid.UUID | None = None,
) -> dict:
    return await narrate_status_answer(
        session,
        question=question,
        context_text=context_text,
        use_opus=use_opus,
        trace_id=trace_id,
    )


def detect_query_mode(question: str, requested_mode: str | None = None) -> str:
    if requested_mode in QUERY_MODES and requested_mode != "answer":
        return requested_mode

    lowered = (question or "").lower()
    if any(phrase in lowered for phrase in ACTIVE_PROJECT_QUERY_HINTS):
        return "active_projects"
    if any(
        phrase in lowered
        for phrase in ("best approach", "what's missing", "what is missing", "holes", "review project", "review this project", "is this the best")
    ):
        return "project_review"
    if "show sources" in lowered or lowered.startswith("sources") or lowered.startswith("show me sources"):
        return "sources"
    if "timeline" in lowered or "story of" in lowered or "walk me through" in lowered:
        return "timeline"
    if "changed since" in lowered or "since yesterday" in lowered or "what changed" in lowered:
        return "changed_since"
    if any(phrase in lowered for phrase in REBOOT_QUERY_HINTS):
        return "latest"
    if "latest" in lowered or "recent" in lowered or "status" in lowered or "what's the latest" in lowered:
        return "latest"
    return "answer"


def parse_since_boundary(question: str, now: datetime) -> datetime | None:
    lowered = (question or "").lower()
    if "yesterday" in lowered:
        return now - timedelta(days=1)

    match = re.search(r"since\s+(\d{4}-\d{2}-\d{2})", lowered)
    if match:
        return datetime.fromisoformat(f"{match.group(1)}T00:00:00+00:00")
    return None


def should_use_web_enrichment(
    question: str,
    *,
    resolved_mode: str,
    resolved_intent: str,
    project_payload: dict | None,
    evidence_quality: dict | None = None,
) -> bool:
    if resolved_mode in {"sources", "timeline", "changed_since", "active_projects"}:
        return False
    if resolved_intent == "exact_fact":
        return False
    lowered = (question or "").strip().lower()
    if project_payload and any(hint in lowered for hint in PERSONAL_QUERY_HINTS):
        return False
    if resolved_intent in {"project_latest", "project_status"} and float((evidence_quality or {}).get("overall", 0.0)) >= 0.55:
        return False
    return True


def candidate_lookup_phrases(question: str) -> list[str]:
    seen: set[str] = set()
    phrases: list[str] = []
    cleaned = re.sub(r"\s+", " ", (question or "").strip())
    if cleaned:
        phrases.append(cleaned)
        seen.add(cleaned.lower())

    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9_.:/-]*", cleaned)
    meaningful = [token for token in tokens if token.lower() not in QUERY_STOPWORDS]
    joined = " ".join(meaningful).strip()
    if joined and joined.lower() not in seen:
        seen.add(joined.lower())
        phrases.append(joined)

    max_window = min(4, len(meaningful))
    for size in range(max_window, 0, -1):
        for start in range(0, len(meaningful) - size + 1):
            phrase = " ".join(meaningful[start : start + size]).strip()
            if len(phrase) < 3:
                continue
            lowered = phrase.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            phrases.append(phrase)
    return phrases[:12]


def _extract_exact_fact_kind(question: str) -> str | None:
    lowered = (question or "").lower()
    if "email" in lowered:
        return "email"
    if any(token in lowered for token in ("url", "website", "site", "link")):
        return "url"
    if any(token in lowered for token in ("ip", "hostname", "host", "droplet", "server address")):
        return "ip"
    if any(token in lowered for token in ("username", "login", "user account", "account user")):
        return "username"
    if any(token in lowered for token in ("port", "id", "identifier", "account number")):
        return "numeric_identifier"
    return None


def _detect_query_intent(question: str, *, resolved_mode: str, project_payload: dict | None) -> str:
    if resolved_mode == "active_projects":
        return "active_projects"
    if resolved_mode in {"timeline", "changed_since"}:
        return "timeline_review"
    if resolved_mode == "project_review":
        return "project_review"
    if _extract_exact_fact_kind(question):
        return "exact_fact"
    if project_payload and resolved_mode in {"latest", "answer"}:
        lowered = (question or "").lower()
        if "status" in lowered:
            return "project_status"
        return "project_latest"
    if resolved_mode == "latest":
        return "latest_status"
    return "general_answer"


def _recency_score(value: str | datetime | None, *, now: datetime) -> float:
    if isinstance(value, str):
        try:
            if value.endswith("Z"):
                value = value.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return 0.2
    else:
        parsed = value
    if parsed is None:
        return 0.2
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    age = now - parsed.astimezone(timezone.utc)
    if age <= timedelta(hours=12):
        return 1.0
    if age <= timedelta(days=1):
        return 0.85
    if age <= timedelta(days=3):
        return 0.65
    if age <= timedelta(days=7):
        return 0.45
    if age <= timedelta(days=30):
        return 0.22
    return 0.08


def _content_alignment(text: str | None, project_title: str | None) -> float:
    if not text or not project_title:
        return 0.0
    return 1.0 if project_title.lower() in text.lower() else 0.0


def _extract_fact_values(text: str) -> dict[str, list[str]]:
    raw_text = text or ""
    urls = re.findall(r"https?://[^\s)>]+", raw_text)
    emails = re.findall(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", raw_text)
    ips = re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", raw_text)
    usernames = re.findall(
        r"(?:user(?:name)?|account)\s*(?:is|:)\s*([A-Za-z0-9_.-]+)",
        raw_text,
        flags=re.IGNORECASE,
    )
    identifiers = re.findall(r"\b\d{4,}\b", raw_text)
    hostnames = [
        value
        for value in re.findall(r"\b(?:[a-zA-Z0-9-]+\.)+[A-Za-z]{2,}\b", raw_text)
        if value not in emails and not value.startswith("http")
    ]
    return {
        "ip": list(dict.fromkeys(ips)),
        "email": list(dict.fromkeys(emails)),
        "url": list(dict.fromkeys(urls)),
        "username": list(dict.fromkeys(usernames)),
        "numeric_identifier": list(dict.fromkeys(identifiers)),
        "hostname": list(dict.fromkeys(hostnames)),
    }


def _build_source_item(
    *,
    source_id: str,
    title: str,
    category: str,
    content: str,
    similarity: float,
    retrieval_kind: str,
    signal_kind: str,
    source_name: str,
    event_time: datetime | None,
    matched_phrases: list[str] | None = None,
    metadata: dict | None = None,
) -> dict:
    event_info = describe_event_time(event_time)
    return {
        "id": source_id,
        "title": title,
        "category": category,
        "content": content[:1200],
        "similarity": round(similarity, 3),
        "retrieval_kind": retrieval_kind,
        "signal_kind": signal_kind,
        "source_name": source_name,
        "matched_phrases": matched_phrases or [],
        "metadata": metadata or {},
        **event_info,
    }


def _coerce_source_item(candidate: dict, *, retrieval_kind: str, fallback_signal_kind: str = "direct_sync") -> dict:
    if "signal_kind" in candidate and "retrieval_kind" in candidate and "event_time_local" in candidate:
        return candidate
    event_time = candidate.get("event_time") or candidate.get("created_at")
    if isinstance(event_time, str):
        try:
            if event_time.endswith("Z"):
                event_time = event_time.replace("Z", "+00:00")
            event_time = datetime.fromisoformat(event_time)
        except ValueError:
            event_time = None
    return _build_source_item(
        source_id=str(candidate.get("id") or "unknown"),
        title=str(candidate.get("title") or "Unknown"),
        category=str(candidate.get("category") or "unknown"),
        content=str(candidate.get("content") or ""),
        similarity=float(candidate.get("similarity", 0.0)),
        retrieval_kind=str(candidate.get("retrieval_kind") or retrieval_kind),
        signal_kind=str(candidate.get("signal_kind") or fallback_signal_kind),
        source_name=str(candidate.get("source_name") or retrieval_kind),
        event_time=event_time,
        matched_phrases=list(candidate.get("matched_phrases") or []),
        metadata=dict(candidate.get("metadata") or {}),
    )


async def resolve_project_payload(session: AsyncSession, question: str) -> dict | None:
    inferred = await infer_project_from_text(session, question)
    if inferred:
        return await build_project_story_payload(session, inferred.id)
    for phrase in candidate_lookup_phrases(question):
        project = await resolve_project(
            session,
            project_hint=phrase,
            source_refs=[phrase],
            create_if_missing=False,
        )
        if project:
            return await build_project_story_payload(session, project.id)
    return None


async def resolve_subject_ref(session: AsyncSession, question: str) -> str | None:
    project_payload = await resolve_project_payload(session, question)
    if project_payload:
        return project_payload["project"]["title"]

    subject_hits = []
    for phrase in candidate_lookup_phrases(question):
        subject_hits = await store.find_story_subjects(session, phrase, limit=3)
        if subject_hits:
            break
    if not subject_hits:
        return None
    for hit in subject_hits:
        if hit.subject_ref:
            return hit.subject_ref
    return subject_hits[0].title


def format_story_context(
    *,
    mode: str,
    intent: str,
    project_payload: dict | None,
    events: list,
    sources: list[dict],
    since_boundary: datetime | None = None,
    evidence_quality: dict | None = None,
) -> str:
    sections: list[str] = [f"Mode: {mode}", f"Intent: {intent}"]
    if since_boundary:
        sections.append(f"Since: {format_display_datetime(since_boundary)}")

    if evidence_quality:
        sections.extend(
            [
                "",
                "Evidence Quality:",
                f"- overall={evidence_quality.get('overall', 0.0):.2f}",
                f"- freshness={evidence_quality.get('freshness', 0.0):.2f}",
                f"- directness={evidence_quality.get('directness', 0.0):.2f}",
                f"- project_alignment={evidence_quality.get('project_alignment', 0.0):.2f}",
                f"- exactness={evidence_quality.get('exactness', 0.0):.2f}",
                f"- contradiction_risk={evidence_quality.get('contradiction_risk', 0.0):.2f}",
            ]
        )

    if project_payload:
        project = project_payload["project"]
        snapshot = project_payload.get("snapshot") or {}
        sections.extend(
            [
                "",
                f"Project: {project['title']}",
                f"Status: {snapshot.get('status') or project['status']}",
                f"Where it stands: {snapshot.get('implemented') or project['content'] or 'No canonical summary.'}",
                f"What changed: {snapshot.get('what_changed') or 'unknown'}",
                f"What is left: {snapshot.get('remaining') or 'unknown'}",
                f"Blockers: {', '.join(snapshot.get('blockers') or []) or 'none'}",
                f"Holes: {', '.join(snapshot.get('holes') or []) or 'none'}",
                f"Why active: {snapshot.get('why_active') or 'unknown'}",
            ]
        )

    if events:
        sections.extend(["", "Story Events:"])
        for event in events:
            event_time = getattr(event, "happened_at", None)
            sections.append(
                " - "
                f"{format_display_datetime(event_time)} | {event.title}"
                f" | signal_kind={signal_kind_for_event(entry_type=getattr(event, 'entry_type', None), actor_type=getattr(event, 'actor_type', None))}"
                f" | summary={event.summary or 'none'}"
                f" | open_question={event.open_question or 'none'}"
            )

    if sources:
        sections.extend(["", "Selected Evidence:"])
        for index, item in enumerate(sources, 1):
            sections.append(
                f"[{index}] {item['category']}: {item['title']} | kind={item['retrieval_kind']} | "
                f"signal={item['signal_kind']} | when={item.get('event_time_local') or 'unknown'} :: {item['content']}"
            )

    return "\n".join(sections).strip()


async def collect_sources(
    session: AsyncSession,
    question: str,
    *,
    category: str | None = None,
    limit: int = 8,
) -> list[dict]:
    query_embedding = await embed_text(question)
    raw_results = await store.vector_search(
        session,
        query_embedding,
        limit=limit * 2,
        min_similarity=0.25,
        category=category,
    )

    items = []
    seen: set[str] = set()
    for chunk in raw_results:
        title = "Unknown"
        resolved_category = chunk.get("resolved_category") or "unknown"
        item_id = str(chunk.get("note_id") or chunk.get("artifact_id"))
        if item_id in seen:
            continue
        seen.add(item_id)

        signal_kind = "direct_sync"
        source_name = "vector"
        event_time = chunk.get("created_at")
        if chunk.get("note_id"):
            note = await store.get_note(session, chunk["note_id"])
            if note:
                title = note.title
                resolved_category = note.category
                source_name = "note"
                event_time = getattr(note, "updated_at", None) or getattr(note, "created_at", None)
        elif chunk.get("artifact_id"):
            artifact = await store.get_artifact(session, chunk["artifact_id"])
            if artifact:
                title = artifact.summary or artifact.content_type
                resolved_category = artifact.content_type
                signal_kind = signal_kind_for_artifact(
                    source=getattr(artifact, "source", None),
                    capture_context=(artifact.metadata_ or {}).get("capture_context"),
                )
                source_name = getattr(artifact, "source", "artifact")
                event_time = getattr(artifact, "created_at", None)

        items.append(
            _build_source_item(
                source_id=item_id,
                title=title,
                category=resolved_category,
                content=chunk["content"],
                similarity=float(chunk["similarity"]),
                retrieval_kind="vector",
                signal_kind=signal_kind,
                source_name=source_name,
                event_time=event_time,
                metadata={"resolved_category": resolved_category},
            )
        )
        if len(items) >= limit:
            break
    return items


async def _collect_exact_sources(
    session: AsyncSession,
    question: str,
    *,
    project_payload: dict | None,
    now: datetime,
    limit: int = 8,
) -> list[dict]:
    try:
        phrases = candidate_lookup_phrases(question)
        project_title = (project_payload or {}).get("project", {}).get("title")
        if project_title and project_title not in phrases:
            phrases.append(project_title)

        exact_sources: list[dict] = []
        seen: set[str] = set()

        artifact_hits = await store.search_artifacts_text(session, phrases, limit=limit * 2)
        for hit in artifact_hits:
            artifact = hit["artifact"]
            source_id = str(artifact.id)
            if source_id in seen:
                continue
            seen.add(source_id)
            signal_kind = signal_kind_for_artifact(
                source=getattr(artifact, "source", None),
                capture_context=(artifact.metadata_ or {}).get("capture_context"),
            )
            matched_count = max(1, len(hit.get("matched_phrases") or []))
            alignment = _content_alignment(
                f"{artifact.summary or ''}\n{artifact.raw_text or ''}",
                project_title,
            )
            similarity = min(0.99, 0.7 + matched_count * 0.08 + alignment * 0.12 + _recency_score(artifact.created_at, now=now) * 0.1)
            exact_sources.append(
                _build_source_item(
                    source_id=source_id,
                    title=artifact.summary or artifact.content_type,
                    category=hit.get("category") or artifact.content_type,
                    content=artifact.raw_text or artifact.summary or artifact.content_type,
                    similarity=similarity,
                    retrieval_kind="exact_artifact",
                    signal_kind=signal_kind,
                    source_name=getattr(artifact, "source", "artifact"),
                    event_time=getattr(artifact, "created_at", None),
                    matched_phrases=hit.get("matched_phrases"),
                    metadata={"capture_intent": hit.get("capture_intent")},
                )
            )

        note_hits = await store.search_notes_text(session, phrases, limit=limit)
        for hit in note_hits:
            note = hit["note"]
            source_id = f"note:{note.id}"
            if source_id in seen:
                continue
            seen.add(source_id)
            matched_count = max(1, len(hit.get("matched_phrases") or []))
            alignment = _content_alignment(f"{note.title}\n{note.content or ''}", project_title)
            similarity = min(0.96, 0.66 + matched_count * 0.08 + alignment * 0.14 + _recency_score(note.updated_at, now=now) * 0.12)
            exact_sources.append(
                _build_source_item(
                    source_id=source_id,
                    title=note.title,
                    category=note.category,
                    content=note.content or note.title,
                    similarity=similarity,
                    retrieval_kind="exact_note",
                    signal_kind="direct_sync",
                    source_name="note",
                    event_time=getattr(note, "updated_at", None) or getattr(note, "created_at", None),
                    matched_phrases=hit.get("matched_phrases"),
                )
            )

        source_item_hits = await store.search_source_items_text(session, phrases, limit=limit)
        for source_item in source_item_hits:
            source_id = f"source:{source_item.id}"
            if source_id in seen:
                continue
            seen.add(source_id)
            content = "\n".join(part for part in (source_item.title, source_item.summary, source_item.external_url) if part)
            similarity = min(0.92, 0.64 + _content_alignment(content, project_title) * 0.14 + _recency_score(source_item.happened_at or source_item.created_at, now=now) * 0.12)
            exact_sources.append(
                _build_source_item(
                    source_id=source_id,
                    title=source_item.title,
                    category="source_item",
                    content=content,
                    similarity=similarity,
                    retrieval_kind="exact_source_item",
                    signal_kind="direct_sync",
                    source_name="source_item",
                    event_time=source_item.happened_at or source_item.created_at,
                )
            )

        exact_sources.sort(key=lambda item: (item["similarity"], item.get("event_time_utc") or ""), reverse=True)
        return exact_sources[:limit]
    except Exception:
        return []


def _build_snapshot_source(project_payload: dict, *, now: datetime) -> dict | None:
    project = project_payload.get("project") or {}
    snapshot = project_payload.get("snapshot") or {}
    if not project:
        return None
    content = "\n".join(
        [
            f"Where it stands: {snapshot.get('implemented') or project.get('content') or 'unknown'}",
            f"What changed: {snapshot.get('what_changed') or 'unknown'}",
            f"What is left: {snapshot.get('remaining') or 'unknown'}",
            f"Blockers: {', '.join(snapshot.get('blockers') or []) or 'none'}",
            f"Holes: {', '.join(snapshot.get('holes') or []) or 'none'}",
        ]
    )
    last_signal = snapshot.get("last_signal_at")
    similarity = min(0.98, 0.78 + _recency_score(last_signal, now=now) * 0.14)
    return _build_source_item(
        source_id=f"snapshot:{project.get('id')}",
        title=f"{project.get('title')} snapshot",
        category="project",
        content=content,
        similarity=similarity,
        retrieval_kind="project_snapshot",
        signal_kind="derived_system",
        source_name="project_snapshot",
        event_time=datetime.fromisoformat(last_signal) if last_signal else None,
        metadata={"project_id": project.get("id")},
    )


def _project_activity_score(entry: dict, *, now: datetime) -> float:
    signal_kind = signal_kind_for_event(entry_type=entry.get("entry_type"), actor_type=entry.get("actor_type"))
    direct_bonus = 0.2 if signal_kind in {"direct_human", "direct_agent"} else 0.05
    recency = _recency_score(entry.get("happened_at"), now=now)
    state_bonus = 0.12 if (entry.get("entry_type") or "") in DIRECT_AGENT_ENTRY_TYPES else 0.0
    return min(0.95, 0.48 + direct_bonus + state_bonus + recency * 0.2)


def _collect_project_sources(project_payload: dict | None, *, now: datetime, limit: int = 8) -> list[dict]:
    if not project_payload:
        return []
    sources: list[dict] = []
    snapshot_source = _build_snapshot_source(project_payload, now=now)
    if snapshot_source:
        sources.append(snapshot_source)

    for entry in (project_payload.get("recent_activity") or [])[:8]:
        signal_kind = signal_kind_for_event(entry_type=entry.get("entry_type"), actor_type=entry.get("actor_type"))
        sources.append(
            _build_source_item(
                source_id=f"event:{entry['id']}",
                title=entry.get("title") or "project event",
                category="story_event",
                content=entry.get("summary") or entry.get("outcome") or entry.get("title") or "project event",
                similarity=_project_activity_score(entry, now=now),
                retrieval_kind="project_event",
                signal_kind=signal_kind,
                source_name="story_event",
                event_time=datetime.fromisoformat(entry["happened_at"]) if entry.get("happened_at") else None,
                metadata={"entry_type": entry.get("entry_type")},
            )
        )

    for item in (project_payload.get("sources") or [])[:4]:
        happened_at = datetime.fromisoformat(item["happened_at"]) if item.get("happened_at") else None
        sources.append(
            _build_source_item(
                source_id=f"project-source:{item['id']}",
                title=item.get("title") or "project source",
                category="source_item",
                content=item.get("summary") or item.get("title") or "project source",
                similarity=min(0.88, 0.55 + _recency_score(happened_at, now=now) * 0.2),
                retrieval_kind="project_source_item",
                signal_kind="direct_sync",
                source_name="project_source_item",
                event_time=happened_at,
            )
        )

    deduped: list[dict] = []
    seen: set[str] = set()
    for item in sorted(sources, key=lambda candidate: candidate["similarity"], reverse=True):
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        deduped.append(item)
        if len(deduped) >= limit:
            break
    return deduped


def _merge_sources(
    *,
    intent: str,
    exact_sources: list[dict],
    project_sources: list[dict],
    vector_sources: list[dict],
    limit: int = 8,
) -> list[dict]:
    if intent == "exact_fact":
        ordered_groups = [exact_sources, project_sources, vector_sources]
    elif intent in {"project_latest", "project_status", "project_review", "timeline_review", "latest_status"}:
        ordered_groups = [project_sources, exact_sources, vector_sources]
    else:
        ordered_groups = [exact_sources, project_sources, vector_sources]

    merged: list[dict] = []
    seen: set[str] = set()
    for group in ordered_groups:
        for item in group:
            if item["id"] in seen:
                continue
            seen.add(item["id"])
            merged.append(item)
            if len(merged) >= limit * 2:
                break
        if len(merged) >= limit * 2:
            break

    merged.sort(
        key=lambda item: (
            item["similarity"],
            1 if item["signal_kind"] in {"direct_human", "direct_agent"} else 0,
            item.get("event_time_utc") or "",
        ),
        reverse=True,
    )
    return merged[:limit]


def _selected_source_text(sources: list[dict]) -> str:
    return "\n".join(item["content"] for item in sources if item.get("content"))


def _build_exact_answer(question: str, sources: list[dict]) -> str | None:
    fact_kind = _extract_exact_fact_kind(question)
    if not fact_kind:
        return None

    values_by_kind: dict[str, list[str]] = {
        "ip": [],
        "email": [],
        "url": [],
        "username": [],
        "numeric_identifier": [],
        "hostname": [],
    }
    for item in sources:
        extracted = _extract_fact_values(item.get("content") or "")
        for key in values_by_kind:
            values_by_kind[key].extend(extracted.get(key, []))
    for key, values in values_by_kind.items():
        values_by_kind[key] = list(dict.fromkeys(values))

    if fact_kind == "ip" and values_by_kind["ip"]:
        answer = f"Direct answer: your droplet IP is `{values_by_kind['ip'][0]}`."
        if any(token in question.lower() for token in ("account", "user", "username")) and values_by_kind["username"]:
            answer += f" The user account in the same evidence is `{values_by_kind['username'][0]}`."
        if len(values_by_kind["ip"]) > 1:
            answer += " I also found additional IP-like values, so review the cited evidence before treating it as final."
        return answer
    if fact_kind == "email" and values_by_kind["email"]:
        return f"Direct answer: the strongest matching email is `{values_by_kind['email'][0]}`."
    if fact_kind == "url" and values_by_kind["url"]:
        return f"Direct answer: the strongest matching URL is {values_by_kind['url'][0]}."
    if fact_kind == "username" and values_by_kind["username"]:
        return f"Direct answer: the strongest matching username is `{values_by_kind['username'][0]}`."
    if fact_kind == "numeric_identifier" and values_by_kind["numeric_identifier"]:
        return f"Direct answer: the strongest matching identifier is `{values_by_kind['numeric_identifier'][0]}`."
    return None


def _build_evidence_quality(
    *,
    sources: list[dict],
    project_payload: dict | None,
    intent: str,
) -> dict:
    if not sources and not project_payload:
        return {
            "overall": 0.0,
            "freshness": 0.0,
            "directness": 0.0,
            "project_alignment": 0.0,
            "exactness": 0.0,
            "contradiction_risk": 0.0,
        }

    freshness = max((float(item.get("similarity", 0.0)) if item.get("event_time_utc") else 0.2 for item in sources), default=0.2)
    directness_weights = {
        "direct_human": 1.0,
        "direct_agent": 0.95,
        "direct_sync": 0.72,
        "derived_system": 0.3,
    }
    directness = sum(directness_weights.get(item["signal_kind"], 0.45) for item in sources[:5]) / max(1, min(len(sources), 5))
    project_title = (project_payload or {}).get("project", {}).get("title")
    project_alignment = 1.0 if project_payload else 0.0
    if project_title and sources:
        project_alignment = max(
            0.35,
            sum(
                1.0 if project_title.lower() in (item["title"] + " " + item["content"]).lower() else 0.0
                for item in sources[:5]
            )
            / max(1, min(len(sources), 5)),
        )
    exactness = 1.0 if any(item["retrieval_kind"].startswith("exact_") for item in sources) else 0.0
    contradiction_risk = 0.05
    if intent == "exact_fact":
        extracted_values = []
        fact_kind = _extract_exact_fact_kind("")
        _ = fact_kind
        for item in sources:
            extracted_values.extend(_extract_fact_values(item["content"]).get("ip", []))
        unique_values = list(dict.fromkeys(extracted_values))
        contradiction_risk = 0.55 if len(unique_values) > 1 else 0.1
    overall = max(
        0.0,
        min(
            1.0,
            freshness * 0.25
            + directness * 0.25
            + project_alignment * 0.2
            + exactness * 0.2
            + (1 - contradiction_risk) * 0.1,
        ),
    )
    return {
        "overall": round(overall, 3),
        "freshness": round(freshness, 3),
        "directness": round(directness, 3),
        "project_alignment": round(project_alignment, 3),
        "exactness": round(exactness, 3),
        "contradiction_risk": round(contradiction_risk, 3),
    }


def _project_events_for_mode(events: list, *, intent: str) -> list:
    if intent not in {"project_latest", "project_status", "latest_status"}:
        return events
    direct = [
        event
        for event in events
        if (getattr(event, "entry_type", "") or "") in DIRECT_AGENT_ENTRY_TYPES
        or signal_kind_for_event(entry_type=getattr(event, "entry_type", None), actor_type=getattr(event, "actor_type", None))
        in {"direct_human", "direct_agent"}
    ]
    if direct:
        return direct[:10]
    return events[:10]


def _sanitize_sources(items: list[dict]) -> list[dict]:
    return [
        {
            key: value
            for key, value in item.items()
            if key not in {"content"}
        }
        for item in items
    ]


async def _persist_trace(
    session: AsyncSession,
    *,
    trace_id: uuid.UUID,
    question: str,
    resolved_mode: str,
    resolved_intent: str,
    failure_stage: str | None,
    evidence_quality: dict,
    used_exact_match: bool,
    used_project_snapshot: bool,
    used_vector_search: bool,
    used_web: bool,
    payload: dict,
) -> None:
    try:
        existing = await store.get_retrieval_trace(session, trace_id)
        values = {
            "question": question,
            "resolved_mode": resolved_mode,
            "resolved_intent": resolved_intent,
            "failure_stage": failure_stage,
            "evidence_quality": evidence_quality,
            "used_exact_match": used_exact_match,
            "used_project_snapshot": used_project_snapshot,
            "used_vector_search": used_vector_search,
            "used_web": used_web,
            "payload": payload,
        }
        if existing:
            await store.update_retrieval_trace(session, trace_id, **values)
            return
        await store.create_retrieval_trace(session, trace_id=trace_id, **values)
    except Exception:
        return


def _failure_result(
    *,
    question: str,
    resolved_mode: str,
    resolved_intent: str,
    trace_id: uuid.UUID,
    failure_stage: str,
    message: str,
) -> dict:
    return {
        "ok": False,
        "mode": resolved_mode,
        "intent": resolved_intent,
        "answer": message,
        "sources": [],
        "brain_sources": [],
        "web_sources": [],
        "events": [],
        "confidence": "low",
        "model": "none",
        "cost_usd": 0,
        "failure_stage": failure_stage,
        "evidence_quality": {
            "overall": 0.0,
            "freshness": 0.0,
            "directness": 0.0,
            "project_alignment": 0.0,
            "exactness": 0.0,
            "contradiction_risk": 0.0,
        },
        "retrieval_trace_id": str(trace_id),
        "used_exact_match": False,
        "used_project_snapshot": False,
        "used_vector_search": False,
        "used_web": False,
        "question": question,
    }


async def build_active_projects_overview(session: AsyncSession, *, limit: int = 6) -> list[dict]:
    await recompute_project_states(session)
    snapshots = await store.list_project_state_snapshots(session, limit=limit * 3)
    rows: list[dict] = []
    for snapshot in snapshots:
        project = await store.get_note(session, snapshot.project_note_id)
        if not project:
            continue
        if snapshot.status not in {"active", "warming_up", "blocked"} and snapshot.manual_state != "pinned":
            continue
        if snapshot.active_score < 0.24 and snapshot.manual_state != "pinned":
            continue
        feature_scores = dict(snapshot.feature_scores or {})
        if is_low_signal_project_name(project.title) and feature_scores.get("git", 0) < 0.25 and feature_scores.get("planning", 0) < 0.2:
            continue
        metadata = dict(snapshot.metadata_ or {})
        rows.append(
            {
                "id": str(project.id),
                "title": project.title,
                "status": snapshot.status,
                "manual_state": snapshot.manual_state,
                "active_score": snapshot.active_score,
                "last_signal_at": str(snapshot.last_signal_at) if snapshot.last_signal_at else None,
                "implemented": snapshot.implemented,
                "remaining": snapshot.remaining,
                "what_changed": snapshot.what_changed,
                "why_active": snapshot.why_active,
                "why_not_active": snapshot.why_not_active,
                "blockers": list(snapshot.blockers or []),
                "holes": list(snapshot.holes or []),
                "feature_scores": feature_scores,
                "repo_count": int(metadata.get("repo_count") or 0),
                "session_count": int(metadata.get("session_count") or 0),
                "planner_mentions": int(metadata.get("planner_mentions") or 0),
                "reminder_count": int(metadata.get("reminder_count") or 0),
            }
        )
    rows.sort(
        key=lambda item: (
            1 if item["manual_state"] == "pinned" else 0,
            float(item["feature_scores"].get("freshness", 0.0)),
            float(item["feature_scores"].get("planning", 0.0)),
            float(item["active_score"]),
        ),
        reverse=True,
    )
    return rows[:limit]


def format_active_projects_context(projects: list[dict]) -> str:
    lines = [
        "Mode: active_projects",
        "",
        "Active Project Board:",
    ]
    for item in projects:
        lines.extend(
            [
                f"- {item['title']} | status={item['status']} | score={item['active_score']:.2f} | last_signal={item['last_signal_at'] or 'unknown'}",
                "  - evidence_counts="
                f"repos:{item.get('repo_count', 0)}"
                f", sessions:{item.get('session_count', 0)}"
                f", planners:{item.get('planner_mentions', 0)}"
                f", reminders:{item.get('reminder_count', 0)}",
                f"  - what_changed={item.get('what_changed') or 'unknown'}",
                f"  - implemented={item.get('implemented') or 'unknown'}",
                f"  - remaining={item.get('remaining') or 'unknown'}",
                f"  - why_active={item.get('why_active') or 'unknown'}",
                f"  - why_not_active={item.get('why_not_active') or 'unknown'}",
            ]
        )
    return "\n".join(lines).strip()


async def query_brain(
    session: AsyncSession,
    *,
    question: str,
    mode: str | None = None,
    category: str | None = None,
    use_opus: bool = False,
    include_web: bool = True,
    now: datetime | None = None,
) -> dict:
    trace_id = uuid.uuid4()
    current_stage = QUERY_STAGE_ROUTING
    current_time = now or datetime.now(timezone.utc)
    resolved_mode = detect_query_mode(question, mode)
    resolved_intent = "general_answer"
    trace_payload: dict[str, Any] = {
        "question": question,
        "candidate_lists": {},
        "selected_evidence": [],
    }

    try:
        if resolved_mode == "active_projects":
            project_payload = None
            resolved_intent = "active_projects"
            projects = await build_active_projects_overview(session)
            evidence_quality = {
                "overall": 0.9 if projects else 0.0,
                "freshness": 0.9 if projects else 0.0,
                "directness": 0.75 if projects else 0.0,
                "project_alignment": 1.0 if projects else 0.0,
                "exactness": 0.0,
                "contradiction_risk": 0.05,
            }
            if not projects:
                result = {
                    "ok": True,
                    "mode": resolved_mode,
                    "intent": resolved_intent,
                    "answer": "I do not have enough grounded project-state evidence to rank active work yet.",
                    "sources": [],
                    "brain_sources": [],
                    "web_sources": [],
                    "events": [],
                    "confidence": "low",
                    "model": "none",
                    "cost_usd": 0,
                    "failure_stage": None,
                    "evidence_quality": evidence_quality,
                    "retrieval_trace_id": str(trace_id),
                    "used_exact_match": False,
                    "used_project_snapshot": True,
                    "used_vector_search": False,
                    "used_web": False,
                }
                await _persist_trace(
                    session,
                    trace_id=trace_id,
                    question=question,
                    resolved_mode=resolved_mode,
                    resolved_intent=resolved_intent,
                    failure_stage=None,
                    evidence_quality=evidence_quality,
                    used_exact_match=False,
                    used_project_snapshot=True,
                    used_vector_search=False,
                    used_web=False,
                    payload={**trace_payload, "projects": []},
                )
                return result

            voice_profile = await store.get_voice_profile(session, "ahmad-default")
            current_stage = QUERY_STAGE_NARRATION
            narration = await narrate_from_context(
                session,
                question=question,
                context_text=(
                    format_active_projects_context(projects)
                    + (
                        f"\n\nVoice Profile:\nSummary: {voice_profile.summary}\nTraits: {voice_profile.traits}"
                        if voice_profile
                        else ""
                    )
                ),
                use_opus=use_opus,
                trace_id=trace_id,
            )
            project_sources = [
                {
                    "id": item["id"],
                    "title": item["title"],
                    "category": "project",
                    "status": item["status"],
                    "active_score": item["active_score"],
                    "similarity": round(float(item["active_score"]), 3),
                    "retrieval_kind": "project_snapshot",
                    "signal_kind": "derived_system",
                    "event_time_utc": item["last_signal_at"],
                    "event_time_local": item["last_signal_at"],
                    "display_timezone": settings.digest_timezone,
                }
                for item in projects
            ]
            result = {
                "ok": True,
                "mode": resolved_mode,
                "intent": resolved_intent,
                "answer": narration["text"],
                "sources": project_sources,
                "brain_sources": project_sources,
                "web_sources": [],
                "events": [],
                "projects": projects,
                "confidence": "high",
                "model": narration["model"],
                "cost_usd": narration["cost_usd"],
                "failure_stage": None,
                "evidence_quality": evidence_quality,
                "retrieval_trace_id": str(trace_id),
                "used_exact_match": False,
                "used_project_snapshot": True,
                "used_vector_search": False,
                "used_web": False,
            }
            await _persist_trace(
                session,
                trace_id=trace_id,
                question=question,
                resolved_mode=resolved_mode,
                resolved_intent=resolved_intent,
                failure_stage=None,
                evidence_quality=evidence_quality,
                used_exact_match=False,
                used_project_snapshot=True,
                used_vector_search=False,
                used_web=False,
                payload={**trace_payload, "projects": projects, "selected_evidence": project_sources, "answer": narration["text"]},
            )
            return result

        project_payload = await resolve_project_payload(session, question)
        resolved_intent = _detect_query_intent(question, resolved_mode=resolved_mode, project_payload=project_payload)
        trace_payload["resolved_project"] = (project_payload or {}).get("project", {}).get("title")

        since_boundary = parse_since_boundary(question, current_time) if resolved_mode == "changed_since" else None

        if project_payload and not project_payload.get("snapshot"):
            await recompute_project_states(session, project_note_ids=[uuid.UUID(project_payload["project"]["id"])])
            project_payload = await build_project_story_payload(session, uuid.UUID(project_payload["project"]["id"]))

        current_stage = QUERY_STAGE_CANDIDATE_RETRIEVAL
        subject_ref = project_payload["project"]["title"] if project_payload else await resolve_subject_ref(session, question)
        project_note_id = uuid.UUID(project_payload["project"]["id"]) if project_payload else None

        event_limit = settings.story_max_events
        if resolved_mode in {"latest", "project_review"}:
            events = await store.list_story_events(
                session,
                project_note_id=project_note_id,
                subject_ref=subject_ref,
                limit=min(10, event_limit),
            )
        elif resolved_mode == "timeline":
            events = await store.list_story_events(
                session,
                project_note_id=project_note_id,
                subject_ref=subject_ref,
                limit=event_limit,
                ascending=True,
            )
        elif resolved_mode == "changed_since":
            events = await store.list_story_events(
                session,
                project_note_id=project_note_id,
                subject_ref=subject_ref,
                since=since_boundary or (current_time - timedelta(days=1)),
                limit=event_limit,
                ascending=True,
            )
        else:
            events = await store.list_story_events(
                session,
                project_note_id=project_note_id,
                subject_ref=subject_ref,
                limit=min(10, event_limit),
            )

        filtered_events = _project_events_for_mode(events, intent=resolved_intent)
        exact_sources = await _collect_exact_sources(
            session,
            question,
            project_payload=project_payload,
            now=current_time,
            limit=8,
        )
        exact_sources = [_coerce_source_item(item, retrieval_kind="exact_artifact") for item in exact_sources]
        project_sources = [_coerce_source_item(item, retrieval_kind="project_snapshot") for item in _collect_project_sources(project_payload, now=current_time, limit=8)]
        vector_sources = [_coerce_source_item(item, retrieval_kind="vector") for item in await collect_sources(session, question, category=category, limit=8)]
        selected_sources = _merge_sources(
            intent=resolved_intent,
            exact_sources=exact_sources,
            project_sources=project_sources,
            vector_sources=vector_sources,
            limit=8,
        )
        evidence_quality = _build_evidence_quality(
            sources=selected_sources,
            project_payload=project_payload,
            intent=resolved_intent,
        )
        used_exact_match = any(item["retrieval_kind"].startswith("exact_") for item in selected_sources)
        used_project_snapshot = any(item["retrieval_kind"] == "project_snapshot" for item in selected_sources)
        used_vector_search = any(item["retrieval_kind"] == "vector" for item in selected_sources)

        trace_payload["candidate_lists"] = {
            "exact": _sanitize_sources(exact_sources),
            "project": _sanitize_sources(project_sources),
            "vector": _sanitize_sources(vector_sources),
        }

        if resolved_mode == "sources":
            answer = "I don't have strong source matches for that yet."
            if selected_sources:
                answer = "\n\n".join(
                    f"[{index}] {item['category']}: {item['title']} ({item['similarity']:.0%})\n{item['content']}"
                    for index, item in enumerate(selected_sources, 1)
                )
            result = {
                "ok": True,
                "mode": resolved_mode,
                "intent": resolved_intent,
                "answer": answer,
                "sources": _sanitize_sources(selected_sources),
                "brain_sources": _sanitize_sources(selected_sources),
                "web_sources": [],
                "events": [],
                "confidence": "medium" if selected_sources else "low",
                "model": "deterministic",
                "cost_usd": 0,
                "failure_stage": None,
                "evidence_quality": evidence_quality,
                "retrieval_trace_id": str(trace_id),
                "used_exact_match": used_exact_match,
                "used_project_snapshot": used_project_snapshot,
                "used_vector_search": used_vector_search,
                "used_web": False,
            }
            await _persist_trace(
                session,
                trace_id=trace_id,
                question=question,
                resolved_mode=resolved_mode,
                resolved_intent=resolved_intent,
                failure_stage=None,
                evidence_quality=evidence_quality,
                used_exact_match=used_exact_match,
                used_project_snapshot=used_project_snapshot,
                used_vector_search=used_vector_search,
                used_web=False,
                payload={**trace_payload, "selected_evidence": _sanitize_sources(selected_sources), "answer": answer},
            )
            return result

        if not filtered_events and not selected_sources and not project_payload:
            result = {
                "ok": True,
                "mode": resolved_mode,
                "intent": resolved_intent,
                "answer": "I don't have enough grounded story context about that yet.",
                "sources": [],
                "brain_sources": [],
                "web_sources": [],
                "events": [],
                "confidence": "low",
                "model": "none",
                "cost_usd": 0,
                "failure_stage": None,
                "evidence_quality": evidence_quality,
                "retrieval_trace_id": str(trace_id),
                "used_exact_match": used_exact_match,
                "used_project_snapshot": used_project_snapshot,
                "used_vector_search": used_vector_search,
                "used_web": False,
            }
            await _persist_trace(
                session,
                trace_id=trace_id,
                question=question,
                resolved_mode=resolved_mode,
                resolved_intent=resolved_intent,
                failure_stage=None,
                evidence_quality=evidence_quality,
                used_exact_match=used_exact_match,
                used_project_snapshot=used_project_snapshot,
                used_vector_search=used_vector_search,
                used_web=False,
                payload={**trace_payload, "selected_evidence": []},
            )
            return result

        exact_answer = _build_exact_answer(question, selected_sources) if resolved_intent == "exact_fact" else None
        context_text = format_story_context(
            mode=resolved_mode,
            intent=resolved_intent,
            project_payload=project_payload,
            events=filtered_events,
            sources=selected_sources,
            since_boundary=since_boundary,
            evidence_quality=evidence_quality,
        )
        voice_profile = await store.get_voice_profile(session, "ahmad-default")
        if voice_profile:
            context_text += f"\n\nVoice Profile:\nSummary: {voice_profile.summary}\nTraits: {voice_profile.traits}"

        current_stage = QUERY_STAGE_NARRATION
        model = "deterministic"
        cost_usd = 0
        final_answer = exact_answer
        if not final_answer:
            if resolved_intent == "exact_fact":
                narration = await narrate_exact_fact_answer(
                    session,
                    question=question,
                    context_text=context_text,
                    use_opus=use_opus,
                    trace_id=trace_id,
                )
            elif resolved_intent in {"timeline_review", "project_review"}:
                narration = await narrate_timeline_answer(
                    session,
                    question=question,
                    context_text=context_text,
                    use_opus=use_opus,
                    trace_id=trace_id,
                )
            else:
                narration = await narrate_from_context(
                    session,
                    question=question,
                    context_text=context_text,
                    use_opus=use_opus,
                    trace_id=trace_id,
                )
            final_answer = narration["text"]
            model = narration["model"]
            cost_usd = narration["cost_usd"]

        web_sources: list[dict] = []
        web_answer = None
        used_web = False
        if include_web and should_use_web_enrichment(
            question,
            resolved_mode=resolved_mode,
            resolved_intent=resolved_intent,
            project_payload=project_payload,
            evidence_quality=evidence_quality,
        ):
            web_payload = await answer_question_with_web(
                question=question,
                context_hints=[
                    project_payload["project"]["title"] if project_payload else None,
                    ((project_payload or {}).get("snapshot") or {}).get("remaining"),
                ],
            )
            if web_payload:
                used_web = True
                web_sources = list(web_payload.get("sources") or [])[:5]
                web_answer = web_payload.get("answer")

        confidence = (
            "high"
            if evidence_quality["overall"] >= 0.75
            else "medium"
            if evidence_quality["overall"] >= 0.45
            else "low"
        )
        if web_answer:
            final_answer = (
                "From your brain:\n"
                f"{final_answer}\n\n"
                "From the web:\n"
                f"{web_answer}"
            )

        result = {
            "ok": True,
            "mode": resolved_mode,
            "intent": resolved_intent,
            "answer": final_answer,
            "sources": [*_sanitize_sources(selected_sources), *web_sources],
            "brain_sources": _sanitize_sources(selected_sources),
            "web_sources": web_sources,
            "events": [
                {
                    "id": str(event.id),
                    "title": event.title,
                    "summary": event.summary,
                    "decision": event.decision,
                    "impact": event.impact,
                    "open_question": event.open_question,
                    "signal_kind": signal_kind_for_event(
                        entry_type=getattr(event, "entry_type", None),
                        actor_type=getattr(event, "actor_type", None),
                    ),
                    **describe_event_time(getattr(event, "happened_at", None)),
                }
                for event in filtered_events
            ],
            "confidence": confidence,
            "model": model,
            "cost_usd": cost_usd,
            "failure_stage": None,
            "evidence_quality": evidence_quality,
            "retrieval_trace_id": str(trace_id),
            "used_exact_match": used_exact_match,
            "used_project_snapshot": used_project_snapshot,
            "used_vector_search": used_vector_search,
            "used_web": used_web,
        }
        await _persist_trace(
            session,
            trace_id=trace_id,
            question=question,
            resolved_mode=resolved_mode,
            resolved_intent=resolved_intent,
            failure_stage=None,
            evidence_quality=evidence_quality,
            used_exact_match=used_exact_match,
            used_project_snapshot=used_project_snapshot,
            used_vector_search=used_vector_search,
            used_web=used_web,
            payload={
                **trace_payload,
                "selected_evidence": _sanitize_sources(selected_sources),
                "events": result["events"],
                "answer": final_answer,
                "web_sources": web_sources,
            },
        )
        return result
    except Exception as exc:
        error_message = str(exc) or "unknown error"
        await _persist_trace(
            session,
            trace_id=trace_id,
            question=question,
            resolved_mode=resolved_mode,
            resolved_intent=resolved_intent,
            failure_stage=current_stage,
            evidence_quality={
                "overall": 0.0,
                "freshness": 0.0,
                "directness": 0.0,
                "project_alignment": 0.0,
                "exactness": 0.0,
                "contradiction_risk": 0.0,
            },
            used_exact_match=False,
            used_project_snapshot=False,
            used_vector_search=False,
            used_web=False,
            payload={**trace_payload, "error": error_message},
        )
        stage_copy = current_stage.replace("_", " ")
        return _failure_result(
            question=question,
            resolved_mode=resolved_mode,
            resolved_intent=resolved_intent,
            trace_id=trace_id,
            failure_stage=current_stage,
            message=f"I hit a {stage_copy} issue before I could answer that cleanly. Try again in a moment.",
        )
