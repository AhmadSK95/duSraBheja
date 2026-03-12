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
from src.services.identity import is_low_signal_project_name, resolve_project
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
)


def detect_query_mode(question: str, requested_mode: str | None = None) -> str:
    if requested_mode in QUERY_MODES:
        return requested_mode

    lowered = (question or "").lower()
    if any(phrase in lowered for phrase in ACTIVE_PROJECT_QUERY_HINTS):
        return "active_projects"
    if any(phrase in lowered for phrase in ("best approach", "what's missing", "what is missing", "holes", "review project", "review this project", "is this the best")):
        return "project_review"
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


def should_use_web_enrichment(question: str, *, resolved_mode: str, project_payload: dict | None) -> bool:
    if resolved_mode in {"sources", "timeline", "changed_since", "active_projects"}:
        return False
    lowered = (question or "").strip().lower()
    if project_payload and any(hint in lowered for hint in PERSONAL_QUERY_HINTS):
        return False
    return True


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
        snapshot = project_payload.get("snapshot") or {}
        sections.extend(
            [
                "",
                f"Project: {project['title']}",
                f"Status: {snapshot.get('status') or project['status']}",
                f"Summary: {project['content'] or 'No canonical summary.'}",
            ]
        )
        if snapshot:
            sections.extend(
                [
                    f"Active Score: {snapshot.get('active_score')}",
                    f"Implemented: {snapshot.get('implemented') or 'unknown'}",
                    f"Remaining: {snapshot.get('remaining') or 'unknown'}",
                    f"Holes: {', '.join(snapshot.get('holes') or []) or 'none'}",
                    f"Risks: {', '.join(snapshot.get('risks') or []) or 'none'}",
                    f"What Changed: {snapshot.get('what_changed') or 'unknown'}",
                    f"Why Active: {snapshot.get('why_active') or 'unknown'}",
                    f"Why Not Active: {snapshot.get('why_not_active') or 'unknown'}",
                ]
            )
        repos = project_payload.get("repos") or []
        if repos:
            repo_lines = ", ".join(repo["name"] for repo in repos[:5] if repo.get("name"))
            if repo_lines:
                sections.append(f"Repos: {repo_lines}")
        connections = project_payload.get("connections") or []
        if connections:
            sections.append("Connections:")
            for item in connections[:5]:
                partner = item["target_ref"] if item["source_ref"] == project["title"] else item["source_ref"]
                sections.append(f" - {partner} | relation={item['relation']} | weight={item['weight']}")
        reminder_items = project_payload.get("reminders") or []
        if reminder_items:
            sections.append("Reminders:")
            for item in reminder_items[:5]:
                sections.append(f" - {item['title']} | next_fire={item.get('next_fire_at') or 'none'}")
        conversation_sessions = project_payload.get("conversation_sessions") or []
        if conversation_sessions:
            sections.append("Conversation Sessions:")
            for item in conversation_sessions[:5]:
                sections.append(
                    f" - {item.get('agent_kind')} | title_hint={item.get('title_hint') or 'none'} | ended={item.get('ended_at') or 'unknown'}"
                )
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
    resolved_mode = detect_query_mode(question, mode)
    current_time = now or datetime.now(timezone.utc)
    if resolved_mode == "active_projects":
        projects = await build_active_projects_overview(session)
        if not projects:
            return {
                "mode": resolved_mode,
                "answer": "I do not have enough grounded project-state evidence to rank active work yet.",
                "sources": [],
                "brain_sources": [],
                "web_sources": [],
                "events": [],
                "confidence": "low",
                "model": "none",
                "cost_usd": 0,
            }
        voice_profile = await store.get_voice_profile(session, "ahmad-default")
        result = await narrate_from_context(
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
            }
            for item in projects
        ]
        return {
            "mode": resolved_mode,
            "answer": result["text"],
            "sources": project_sources,
            "brain_sources": project_sources,
            "web_sources": [],
            "events": [],
            "projects": projects,
            "confidence": "high",
            "model": result["model"],
            "cost_usd": result["cost_usd"],
        }
    since_boundary = parse_since_boundary(question, current_time) if resolved_mode == "changed_since" else None

    project_payload = await resolve_project_payload(session, question)
    if project_payload and not project_payload.get("snapshot"):
        await recompute_project_states(session, project_note_ids=[uuid.UUID(project_payload["project"]["id"])])
        project_payload = await build_project_story_payload(session, uuid.UUID(project_payload["project"]["id"]))
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

    sources = await collect_sources(session, question, category=category, limit=8)
    voice_profile = await store.get_voice_profile(session, "ahmad-default")

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
            "brain_sources": [{k: v for k, v in item.items() if k != "content"} for item in sources],
            "web_sources": [],
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
            "brain_sources": [],
            "web_sources": [],
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
        context_text=(
            context_text
            + (
                f"\n\nVoice Profile:\nSummary: {voice_profile.summary}\nTraits: {voice_profile.traits}"
                if voice_profile
                else ""
            )
        ),
        use_opus=use_opus,
        trace_id=trace_id,
    )
    web_sources: list[dict] = []
    web_answer = None
    if include_web and should_use_web_enrichment(question, resolved_mode=resolved_mode, project_payload=project_payload):
        web_payload = await answer_question_with_web(
            question=question,
            context_hints=[
                project_payload["project"]["title"] if project_payload else None,
                ((project_payload or {}).get("snapshot") or {}).get("remaining"),
            ],
        )
        if web_payload:
            web_sources = list(web_payload.get("sources") or [])[:5]
            web_answer = web_payload.get("answer")
    confidence = "high" if project_payload and (events or project_payload.get("snapshot")) else "medium" if events or sources else "low"
    final_answer = result["text"]
    if web_answer:
        final_answer = (
            "From your brain:\n"
            f"{result['text']}\n\n"
            "From the web:\n"
            f"{web_answer}"
        )
    return {
        "mode": resolved_mode,
        "answer": final_answer,
        "sources": [
            *[{k: v for k, v in item.items() if k != "content"} for item in sources],
            *web_sources,
        ],
        "brain_sources": [{k: v for k, v in item.items() if k != "content"} for item in sources],
        "web_sources": web_sources,
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
