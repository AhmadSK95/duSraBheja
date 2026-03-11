"""Classifier agent — Claude Haiku 4.5 structured classification."""

import json
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.base import agent_call
from src.config import settings

SYSTEM_PROMPT = """You are a personal knowledge classifier for a second brain system.

Classify the input into exactly ONE of these categories:
- task: An actionable item with a clear thing to do
- project: A multi-step initiative or ongoing effort
- people: Information about a person (name, contact, relationship, notes)
- idea: A brainstorm, concept, possibility, or creative thought
- note: General knowledge, reference material, learning, or durable notes
- resource: A document, link, reference, guide, or asset worth reusing
- reminder: A time-bound alert or thing to remember at a specific time
- daily_planner: A daily plan, schedule, or agenda
- weekly_planner: A weekly plan, schedule, or agenda

For each input, return a JSON object with these exact fields:
{
  "category": "one of the 9 categories above",
  "confidence": 0.0 to 1.0,
  "entities": [{"type": "person|project|topic|date|url", "value": "extracted value"}],
  "tags": ["relevant", "tags"],
  "priority": "low|medium|high|urgent",
  "suggested_action": "brief suggestion of what to do with this",
  "summary": "one-line summary of the input"
}

Rules:
- Return ONLY valid JSON, no markdown fences or extra text
- confidence should reflect how certain you are about the category
- Extract ALL named entities (people, projects, dates, URLs)
- If the input mentions a deadline or urgency, set priority accordingly
- If you're unsure between two categories, pick the most likely one but lower the confidence"""


async def classify(
    session: AsyncSession,
    text: str,
    trace_id: uuid.UUID | None = None,
) -> dict:
    """Classify text using Claude Haiku 4.5.

    Returns parsed dict with category, confidence, entities, etc.
    """
    result = await agent_call(
        session,
        agent_name="classifier",
        action="classify",
        prompt=text,
        system=SYSTEM_PROMPT,
        model=settings.classifier_model,
        max_tokens=1024,
        temperature=0.0,
        trace_id=trace_id,
    )

    parsed = json.loads(result["text"])
    parsed["_meta"] = {
        "model": result["model"],
        "tokens_used": result["input_tokens"] + result["output_tokens"],
        "cost_usd": result["cost_usd"],
        "duration_ms": result["duration_ms"],
    }
    return parsed


async def reclassify(
    session: AsyncSession,
    original_text: str,
    user_clarification: str,
    trace_id: uuid.UUID | None = None,
) -> dict:
    """Re-classify after user provides clarification."""
    prompt = f"""Original input: {original_text}

User clarification: {user_clarification}

Classify this input considering the user's clarification."""

    result = await agent_call(
        session,
        agent_name="classifier",
        action="reclassify",
        prompt=prompt,
        system=SYSTEM_PROMPT,
        model=settings.classifier_model,
        max_tokens=1024,
        temperature=0.0,
        trace_id=trace_id,
    )

    parsed = json.loads(result["text"])
    parsed["_meta"] = {
        "model": result["model"],
        "tokens_used": result["input_tokens"] + result["output_tokens"],
        "cost_usd": result["cost_usd"],
        "duration_ms": result["duration_ms"],
    }
    return parsed
