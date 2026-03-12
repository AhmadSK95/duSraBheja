"""Claude-powered storyteller helpers for grounded story events and narratives."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.base import agent_call
from src.config import settings
from src.lib.llm_json import parse_json_object

STORY_EVENT_SYSTEM_PROMPT = """You convert project/activity context into a grounded story event.

Return ONLY valid JSON with this exact shape:
{
  "subject_type": "project|idea|person|topic",
  "subject_ref": "best canonical subject name or null",
  "entry_type": "brief machine-friendly event type",
  "title": "short event title",
  "summary": "1-2 sentence factual summary",
  "decision": "decision that was made or null",
  "rationale": "why that decision happened or null",
  "constraint": "blocking constraint, risk, or pressure or null",
  "outcome": "what happened because of it or null",
  "impact": "why it matters next or null",
  "open_question": "what remains unresolved or null",
  "evidence_refs": ["small factual evidence anchors pulled from the input"],
  "tags": ["short", "tags"]
}

Rules:
- Stay factual and grounded in the input.
- Do not invent decisions or intent if the input does not support them.
- If the input is mostly a snapshot, summarize it as a state update.
- Prefer concise evidence anchors over long quotes.
"""

NARRATIVE_SYSTEM_PROMPT = """You are Ahmad's storyteller brain.

Answer using ONLY the provided evidence.
Write as a factual narrative, not fiction.

Structure:
1. Where things stand
2. What led here
3. Turning points
4. What changed recently
5. Unresolved tension
6. Sources

Rules:
- Keep Ahmad's tone: concise, grounded, direct, story-aware.
- Do not distort dates, names, or citations.
- Cite sources as [1], [2], etc.
- If evidence is thin, say that clearly.
"""


async def extract_story_event(
    session: AsyncSession,
    *,
    title: str,
    body_markdown: str,
    project_ref: str | None = None,
    actor_name: str | None = None,
    trace_id: uuid.UUID | None = None,
) -> dict:
    prompt = f"""Title: {title}
Project Ref: {project_ref or "none"}
Actor: {actor_name or "unknown"}

Input:
{body_markdown}
"""
    result = await agent_call(
        session,
        agent_name="storyteller",
        action="extract_story_event",
        prompt=prompt,
        system=STORY_EVENT_SYSTEM_PROMPT,
        model=settings.sonnet_model,
        max_tokens=1200,
        temperature=0.1,
        trace_id=trace_id,
    )
    parsed = parse_json_object(result["text"])
    parsed.setdefault("subject_type", "topic")
    parsed.setdefault("evidence_refs", [])
    parsed.setdefault("tags", [])
    return parsed


async def narrate_from_context(
    session: AsyncSession,
    *,
    question: str,
    context_text: str,
    use_opus: bool = False,
    trace_id: uuid.UUID | None = None,
) -> dict:
    prompt = f"""Question: {question}

Context:
{context_text}

Answer as a grounded story. Cite sources using [1], [2], etc."""
    model = settings.opus_model if use_opus else settings.sonnet_model
    return await agent_call(
        session,
        agent_name="storyteller",
        action="narrate",
        prompt=prompt,
        system=NARRATIVE_SYSTEM_PROMPT,
        model=model,
        max_tokens=1800,
        temperature=0.2,
        trace_id=trace_id,
    )
