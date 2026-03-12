"""Story-aware query service for latest, timeline, change, and evidence modes."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.storyteller import narrate_from_context
from src.config import settings
from src.constants import QUERY_MODES
from src.lib import store
from src.lib.embeddings import embed_text
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
    "to",
    "update",
    "updates",
    "what",
    "whats",
    "yesterday",
}


def detect_query_mode(question: str, requested_mode: str | None = None) -> str:
    if requested_mode in QUERY_MODES:
        return requested_mode

    lowered = (question or "").lower()
    if "show sources" in lowered or lowered.startswith("sources") or lowered.startswith("show me sources"):
        return "sources"
    if "timeline" in lowered or "story of" in lowered or "walk me through" in lowered:
        return "timeline"
    if "changed since" in lowered or "since yesterday" in lowered or "what changed" in lowered:
        return "changed_since"
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


def candidate_lookup_phrases(question: str) -> list[str]:
    seen: set[str] = set()
    phrases: list[str] = []
    cleaned = re.sub(r"\s+", " ", (question or "").strip())
    if cleaned:
        phrases.append(cleaned)
        seen.add(cleaned.lower())

    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]*", cleaned)
    meaningful = [token for token in tokens if token.lower() not in QUERY_STOPWORDS]
    joined = " ".join(meaningful).strip()
    if joined and joined.lower() not in seen:
        seen.add(joined.lower())
        phrases.append(joined)

    max_window = min(4, len(meaningful))
    for size in range(max_window, 0, -1):
        for start in range(0, len(meaningful) - size + 1):
            phrase = " ".join(meaningful[start : start + size]).strip()
            if len(phrase) < 4:
                continue
            lowered = phrase.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            phrases.append(phrase)
    return phrases[:12]


async def resolve_project_payload(session: AsyncSession, question: str) -> dict | None:
    for phrase in candidate_lookup_phrases(question):
        matches = await store.find_notes_by_title(session, phrase, "project")
        if matches:
            return await build_project_story_payload(session, matches[0].id)
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
    project_payload: dict | None,
    events: list,
    sources: list[dict],
    since_boundary: datetime | None = None,
) -> str:
    sections: list[str] = [f"Mode: {mode}"]
    if since_boundary:
        sections.append(f"Since: {since_boundary.isoformat()}")

    if project_payload:
        project = project_payload["project"]
        sections.extend(
            [
                "",
                f"Project: {project['title']}",
                f"Status: {project['status']}",
                f"Summary: {project['content'] or 'No canonical summary.'}",
            ]
        )
        repos = project_payload.get("repos") or []
        if repos:
            repo_lines = ", ".join(repo["name"] for repo in repos[:5] if repo.get("name"))
            if repo_lines:
                sections.append(f"Repos: {repo_lines}")
        project_sources = project_payload.get("sources") or []
        if project_sources:
            sections.append("Project Sources:")
            for item in project_sources[:5]:
                sections.append(
                    f" - {item.get('title')} | summary={item.get('summary') or 'none'} | when={item.get('happened_at') or 'unknown'}"
                )

    if events:
        sections.extend(["", "Story Events:"])
        for event in events:
            evidence = ", ".join(event.evidence_refs[:3]) if getattr(event, "evidence_refs", None) else ""
            sections.append(
                " - "
                f"{event.happened_at.isoformat()}: {event.title}"
                f" | summary={event.summary or 'none'}"
                f" | decision={event.decision or 'none'}"
                f" | rationale={event.rationale or 'none'}"
                f" | constraint={event.constraint or 'none'}"
                f" | outcome={event.outcome or 'none'}"
                f" | impact={event.impact or 'none'}"
                f" | open_question={event.open_question or 'none'}"
                f" | evidence={evidence or 'none'}"
            )

    if sources:
        sections.extend(["", "Sources:"])
        for index, item in enumerate(sources, 1):
            sections.append(f"[{index}] {item['category']}: {item['title']} :: {item['content']}")

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

        if chunk.get("note_id"):
            note = await store.get_note(session, chunk["note_id"])
            if note:
                title = note.title
                resolved_category = note.category
        elif chunk.get("artifact_id"):
            artifact = await store.get_artifact(session, chunk["artifact_id"])
            if artifact:
                title = artifact.summary or artifact.content_type
                resolved_category = artifact.content_type

        items.append(
            {
                "id": item_id,
                "title": title,
                "category": resolved_category,
                "similarity": round(chunk["similarity"], 3),
                "content": chunk["content"][:800],
            }
        )
        if len(items) >= limit:
            break
    return items


async def query_brain(
    session: AsyncSession,
    *,
    question: str,
    mode: str | None = None,
    category: str | None = None,
    use_opus: bool = False,
    now: datetime | None = None,
) -> dict:
    trace_id = uuid.uuid4()
    resolved_mode = detect_query_mode(question, mode)
    current_time = now or datetime.now(timezone.utc)
    since_boundary = parse_since_boundary(question, current_time) if resolved_mode == "changed_since" else None

    project_payload = await resolve_project_payload(session, question)
    subject_ref = project_payload["project"]["title"] if project_payload else await resolve_subject_ref(session, question)
    project_note_id = uuid.UUID(project_payload["project"]["id"]) if project_payload else None

    event_limit = settings.story_max_events
    if resolved_mode == "latest":
        events = await store.list_story_events(
            session,
            project_note_id=project_note_id,
            subject_ref=subject_ref,
            limit=min(8, event_limit),
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

    sources = await collect_sources(session, question, category=category, limit=8)

    if resolved_mode == "sources":
        if not sources:
            answer = "I don't have strong source matches for that yet."
        else:
            answer = "\n\n".join(
                f"[{index}] {item['category']}: {item['title']} ({item['similarity']:.0%})\n{item['content']}"
                for index, item in enumerate(sources, 1)
            )
        return {
            "mode": resolved_mode,
            "answer": answer,
            "sources": [{k: v for k, v in item.items() if k != "content"} for item in sources],
            "events": [],
            "confidence": "medium" if sources else "low",
            "model": "deterministic",
            "cost_usd": 0,
        }

    if not events and not sources and not project_payload:
        return {
            "mode": resolved_mode,
            "answer": "I don't have enough grounded story context about that yet.",
            "sources": [],
            "events": [],
            "confidence": "low",
            "model": "none",
            "cost_usd": 0,
        }

    context_text = format_story_context(
        mode=resolved_mode,
        project_payload=project_payload,
        events=events,
        sources=sources,
        since_boundary=since_boundary,
    )
    result = await narrate_from_context(
        session,
        question=question,
        context_text=context_text,
        use_opus=use_opus,
        trace_id=trace_id,
    )
    confidence = "high" if project_payload and events else "medium" if events or sources else "low"
    return {
        "mode": resolved_mode,
        "answer": result["text"],
        "sources": [{k: v for k, v in item.items() if k != "content"} for item in sources],
        "events": [
            {
                "id": str(event.id),
                "title": event.title,
                "summary": event.summary,
                "decision": event.decision,
                "impact": event.impact,
                "open_question": event.open_question,
                "happened_at": str(event.happened_at),
            }
            for event in events
        ],
        "confidence": confidence,
        "model": result["model"],
        "cost_usd": result["cost_usd"],
    }
