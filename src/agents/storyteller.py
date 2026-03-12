"""Claude-powered storyteller helpers for grounded story events and narratives."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.base import agent_call
from src.config import settings
from src.lib.llm_json import LLMJSONError, parse_json_object

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
2. What is already implemented or proven
3. What is left, unproven, or still in motion
4. What led here / turning points
5. Approach assessment: what looks strong and what looks weak
6. Misses, holes, or hidden risks
7. What changed recently
8. Sources

Rules:
- Keep Ahmad's tone: concise, grounded, direct, story-aware.
- Do not distort dates, names, or citations.
- Cite sources as [1], [2], etc.
- If the evidence is too thin to judge what is implemented, left, or weak, say that explicitly instead of guessing.
- If evidence is thin, say that clearly.
"""

DIGEST_SYSTEM_PROMPT = """You compose Ahmad's morning operating brief from grounded brain signals.

Return ONLY valid JSON with this exact shape:
{
  "headline": "one-line headline",
  "narrative": "short operating brief in Ahmad's voice",
  "improvement_focus": [
    {"title": "where Ahmad can improve by working here", "why": "why this matters now"}
  ],
  "low_confidence_sections": ["section names that still feel weak"],
  "recommended_tasks": [
    {"title": "task", "why": "why it matters now", "project_ref": "project or null"}
  ],
  "project_assessments": [
    {
      "project": "project name",
      "where_it_stands": "current state",
      "implemented": "what seems done or proven",
      "left": "what is still left or unclear",
      "holes": "weaknesses, misses, or risks",
      "next_step": "best immediate move"
    }
  ],
  "writing_topics": [
    {"title": "topic", "why": "why now"}
  ],
  "video_recommendations": [
    {"title": "short title", "search_query": "youtube search to run", "why": "why this video would help"}
  ],
  "brain_teasers": [
    {"title": "short label", "prompt": "the teaser or puzzle", "hint": "small hint"}
  ]
}

Rules:
- Stay grounded in the supplied context.
- Recommended tasks should feel like strong next bets, not generic todos.
- Project assessments should say what is built, what is left, and where the holes are.
- For video_recommendations, never invent a direct YouTube URL or fake creator attribution. If you do not have grounded links, output useful YouTube search ideas instead.
- Brain teasers can be generated, but make them thoughtful and relevant to the current work when possible.
- If a section is weak because evidence is thin, say so and add that section name to low_confidence_sections.
- Keep lists tight: up to 10 tasks, 5 project assessments, 5 writing topics, 5 video recommendations, 5 brain teasers.
"""

JSON_REPAIR_SYSTEM_PROMPT = """You repair malformed JSON.

Return ONLY valid JSON.
Do not wrap in markdown fences.
Do not add commentary.
Preserve the original schema and content as closely as possible.
If a field is missing, add the smallest valid empty value for that field.
"""

PROJECT_STATE_SYSTEM_PROMPT = """You convert grounded project evidence into a durable project state snapshot.

Return ONLY valid JSON with this exact shape:
{
  "implemented": "what is already built, proven, or clearly in place",
  "remaining": "what is left, uncertain, or still moving",
  "blockers": ["explicit blockers or constraints"],
  "risks": ["meaningful risks or blind spots"],
  "holes": ["misses, weak spots, or things Ahmad is probably not looking at enough"],
  "what_changed": "what changed recently",
  "why_active": "why this project should count as active right now",
  "why_not_active": "why it may not deserve active focus right now",
  "confidence": 0.0
}

Rules:
- Stay factual and grounded in the evidence.
- Separate what is implemented from what is merely discussed.
- If evidence is thin, say that explicitly in remaining/holes and lower confidence.
- Prefer specific weaknesses over generic criticism.
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


async def compose_digest_sections(
    session: AsyncSession,
    *,
    digest_date: str,
    trigger: str,
    context_text: str,
    trace_id: uuid.UUID | None = None,
) -> dict:
    prompt = f"""Digest date: {digest_date}
Trigger: {trigger}

Context:
{context_text}

Return the JSON operating brief."""
    result = await agent_call(
        session,
        agent_name="storyteller",
        action="compose_digest",
        prompt=prompt,
        system=DIGEST_SYSTEM_PROMPT,
        model=settings.opus_model,
        max_tokens=2200,
        temperature=0.25,
        trace_id=trace_id,
    )
    response_text = result["text"].strip()
    try:
        return parse_json_object(response_text)
    except LLMJSONError:
        repair = await agent_call(
            session,
            agent_name="storyteller",
            action="repair_digest_json",
            prompt=f"Repair this into valid JSON using the original required schema only:\n\n{response_text}",
            system=JSON_REPAIR_SYSTEM_PROMPT,
            model=settings.sonnet_model,
            max_tokens=2200,
            temperature=0.0,
            trace_id=trace_id,
        )
        return parse_json_object(repair["text"])


async def assess_project_state(
    session: AsyncSession,
    *,
    project_name: str,
    context_text: str,
    trace_id: uuid.UUID | None = None,
) -> dict:
    prompt = f"""Project: {project_name}

Evidence:
{context_text}

Return the JSON project state snapshot."""
    result = await agent_call(
        session,
        agent_name="storyteller",
        action="assess_project_state",
        prompt=prompt,
        system=PROJECT_STATE_SYSTEM_PROMPT,
        model=settings.opus_model,
        max_tokens=1600,
        temperature=0.15,
        trace_id=trace_id,
    )
    response_text = result["text"].strip()
    try:
        return parse_json_object(response_text)
    except LLMJSONError:
        repair = await agent_call(
            session,
            agent_name="storyteller",
            action="repair_project_state_json",
            prompt=f"Repair this into valid JSON using the original required schema only:\n\n{response_text}",
            system=JSON_REPAIR_SYSTEM_PROMPT,
            model=settings.sonnet_model,
            max_tokens=1600,
            temperature=0.0,
            trace_id=trace_id,
        )
        return parse_json_object(repair["text"])
