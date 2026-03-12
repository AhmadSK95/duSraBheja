"""Retriever agent — hybrid project-aware RAG with citations."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.base import agent_call
from src.config import settings
from src.lib.embeddings import embed_text
from src.lib.store import find_notes_by_title, get_artifact, get_note, vector_search
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
    "status",
    "the",
    "to",
    "update",
    "updates",
    "what",
    "whats",
}
STATUS_HINTS = {"latest", "recent", "status", "update", "updates", "progress", "story"}


def build_system_prompt() -> str:
    prompt = """You are the Retriever agent for duSraBheja, Ahmad's personal second brain.

Answer the question using ONLY the provided context. If the context doesn't contain
enough information, say so honestly.

Rules:
- Cite sources using [1], [2], etc.
- Be concise and direct
- If you're synthesizing from multiple sources, make that clear
- Include dates when they're relevant to the answer
- Don't make up information not in the context"""

    voice = (settings.brain_voice_instructions or "").strip()
    if voice:
        prompt += f"\n- Match Ahmad's voice and tone using these instructions: {voice}"
    return prompt


def _question_looks_status_like(question: str) -> bool:
    lowered = (question or "").lower()
    return any(token in lowered for token in STATUS_HINTS)


def _candidate_lookup_phrases(question: str) -> list[str]:
    seen: set[str] = set()
    phrases: list[str] = []

    cleaned_question = re.sub(r"\s+", " ", (question or "").strip())
    if cleaned_question:
        phrases.append(cleaned_question)
        seen.add(cleaned_question.lower())

    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]*", cleaned_question)
    meaningful = [token for token in tokens if token.lower() not in QUERY_STOPWORDS]

    joined = " ".join(meaningful).strip()
    if joined and joined.lower() not in seen:
        phrases.append(joined)
        seen.add(joined.lower())

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


def _build_project_story_context(project_payload: dict) -> str:
    project = project_payload["project"]
    sections = [
        f"Project: {project['title']}",
        f"Status: {project['status']}",
        f"Summary: {project['content'] or 'No canonical project summary yet.'}",
    ]

    repos = project_payload.get("repos") or []
    if repos:
        sections.extend(
            [
                "",
                "Repos:",
                *[
                    f"- {repo['name']} ({repo.get('branch') or 'unknown branch'})"
                    for repo in repos[:5]
                ],
            ]
        )

    recent_activity = project_payload.get("recent_activity") or []
    if recent_activity:
        sections.extend(
            [
                "",
                "Recent Project Activity:",
                *[
                    f"- {entry['happened_at']}: {entry['title']} | {entry.get('summary') or 'No summary'}"
                    for entry in recent_activity[:8]
                ],
            ]
        )

    sources = project_payload.get("sources") or []
    if sources:
        sections.extend(
            [
                "",
                "Linked Sources:",
                *[
                    f"- {item['title']}: {item.get('summary') or 'No summary'}"
                    for item in sources[:5]
                ],
            ]
        )

    return "\n".join(sections).strip()


async def _find_relevant_project_payload(session: AsyncSession, question: str) -> dict | None:
    for phrase in _candidate_lookup_phrases(question):
        matches = await find_notes_by_title(session, phrase, "project")
        if matches:
            return await build_project_story_payload(session, matches[0].id)
    return None


async def _find_title_match_notes(
    session: AsyncSession,
    question: str,
    *,
    category: str | None,
    limit: int = 5,
):
    notes = []
    seen: set[str] = set()
    for phrase in _candidate_lookup_phrases(question):
        matches = await find_notes_by_title(session, phrase, category)
        for note in matches:
            note_id = str(note.id)
            if note_id in seen:
                continue
            seen.add(note_id)
            notes.append(note)
            if len(notes) >= limit:
                return notes
    return notes


def _similarity_from_phrase_match(question: str, title: str) -> float:
    lowered_question = (question or "").lower()
    lowered_title = (title or "").lower()
    if lowered_title and lowered_title in lowered_question:
        return 0.95
    return 0.82


async def answer_question(
    session: AsyncSession,
    question: str,
    category: str | None = None,
    use_opus: bool = False,
    trace_id: uuid.UUID | None = None,
) -> dict:
    """Hybrid pipeline: project/title retrieval + vector search + synthesis."""
    trace_id = trace_id or uuid.uuid4()

    query_embedding = await embed_text(question)
    raw_results = await vector_search(
        session,
        query_embedding,
        limit=20,
        min_similarity=0.3,
        category=category,
    )

    title_match_notes = await _find_title_match_notes(session, question, category=category)
    project_payload = await _find_relevant_project_payload(session, question)

    if not raw_results and not title_match_notes and not project_payload:
        return {
            "answer": "I don't have any relevant information about that in my brain yet.",
            "sources": [],
            "confidence": "low",
            "model": "none",
            "cost_usd": 0,
        }

    now = datetime.now(timezone.utc)
    for result in raw_results:
        days_old = 0
        created_at = result.get("created_at")
        if created_at:
            days_old = max(0, (now - created_at).days)
        recency = max(0, 1 - days_old / 365)
        result["rerank_score"] = 0.7 * result["similarity"] + 0.3 * recency

    raw_results.sort(key=lambda item: item["rerank_score"], reverse=True)
    top_results = raw_results[:8]

    context_items = []
    seen_context_ids: set[str] = set()

    if project_payload and (category in {None, "project"} or _question_looks_status_like(question)):
        project_id = project_payload["project"]["id"]
        seen_context_ids.add(f"note:{project_id}")
        context_items.append(
            {
                "id": project_id,
                "title": project_payload["project"]["title"],
                "category": "project_story",
                "similarity": 0.96,
                "content": _build_project_story_context(project_payload),
            }
        )

    for note in title_match_notes:
        note_key = f"note:{note.id}"
        if note_key in seen_context_ids:
            continue
        seen_context_ids.add(note_key)
        context_items.append(
            {
                "id": str(note.id),
                "title": note.title,
                "category": note.category,
                "similarity": _similarity_from_phrase_match(question, note.title),
                "content": note.content or note.title,
            }
        )

    for chunk in top_results:
        title = "Unknown"
        resolved_category = chunk.get("resolved_category") or "unknown"
        context_id = None

        if chunk.get("note_id"):
            note = await get_note(session, chunk["note_id"])
            if note:
                title = note.title
                resolved_category = note.category
                context_id = f"note:{note.id}"
        elif chunk.get("artifact_id"):
            artifact = await get_artifact(session, chunk["artifact_id"])
            if artifact:
                title = artifact.summary or artifact.content_type
                resolved_category = "artifact"
                context_id = f"artifact:{artifact.id}"

        if context_id and context_id in seen_context_ids:
            continue
        if context_id:
            seen_context_ids.add(context_id)

        context_items.append(
            {
                "id": str(chunk.get("note_id") or chunk.get("artifact_id")),
                "title": title,
                "category": resolved_category,
                "similarity": round(chunk["similarity"], 3),
                "content": chunk["content"],
            }
        )

    context_items = context_items[:8]

    sources = [
        {
            "id": item["id"],
            "title": item["title"],
            "category": item["category"],
            "similarity": round(item["similarity"], 3),
        }
        for item in context_items
    ]
    context_text = "\n\n".join(
        f"[{index}] ({item['category']}: {item['title']}) {item['content']}"
        for index, item in enumerate(context_items, 1)
    )

    prompt = f"""Question: {question}

Context:
{context_text}

Answer the question. Cite your sources using [1], [2], etc."""

    model = settings.opus_model if use_opus else settings.sonnet_model
    result = await agent_call(
        session,
        agent_name="retriever",
        action="synthesize",
        prompt=prompt,
        system=build_system_prompt(),
        model=model,
        max_tokens=2048,
        temperature=0.1,
        trace_id=trace_id,
    )

    average_similarity = sum(item["similarity"] for item in context_items) / len(context_items)
    if project_payload and project_payload.get("recent_activity"):
        confidence = "high"
    elif average_similarity > 0.65 and len(context_items) >= 3:
        confidence = "high"
    elif average_similarity > 0.45 or title_match_notes or project_payload:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "answer": result["text"],
        "sources": sources,
        "confidence": confidence,
        "model": result["model"],
        "cost_usd": result["cost_usd"],
    }
