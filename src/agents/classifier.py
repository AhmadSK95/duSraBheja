"""Classifier agent — Claude Haiku 4.5 structured classification."""

import re
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.base import agent_call
from src.config import settings
from src.constants import normalize_category, normalize_tags
from src.lib.llm_json import LLMJSONError, parse_json_object
from src.services.capture_analysis import infer_capture_intent, validate_capture
from src.services.planner import detect_planner_scope, extract_planner_dates

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
  "capture_intent": "thought|idea|question|critique|plan_capture|status_update|reference|reminder_request",
  "intent_confidence": 0.0 to 1.0,
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
- If you're unsure between two categories, pick the most likely one but lower the confidence
- capture_intent should describe what the user is doing with the capture, independent of storage category
- For planner images or handwritten pages:
  - daily_planner means one day/page, even if it has many bullets
  - weekly_planner means multiple dated sections, multiple weekday headers, or an explicit weekly scope
  - never infer a weekly planner from bullet count alone"""


async def classify(
    session: AsyncSession,
    text: str,
    *,
    content_type: str | None = None,
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

    parsed = _parse_classifier_response(result["text"], text, content_type=content_type)
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
    *,
    content_type: str | None = None,
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

    parsed = _parse_classifier_response(result["text"], original_text, content_type=content_type)
    parsed["_meta"] = {
        "model": result["model"],
        "tokens_used": result["input_tokens"] + result["output_tokens"],
        "cost_usd": result["cost_usd"],
        "duration_ms": result["duration_ms"],
    }
    return parsed


def _parse_classifier_response(response_text: str, original_text: str, *, content_type: str | None = None) -> dict:
    try:
        parsed = parse_json_object(response_text)
    except LLMJSONError:
        parsed = _fallback_classification(original_text)

    category = normalize_category(parsed.get("category"))
    confidence = parsed.get("confidence", 0.4)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.4
    confidence = min(max(confidence, 0.0), 1.0)

    entities = []
    for entity in parsed.get("entities") or []:
        if not isinstance(entity, dict):
            continue
        entity_type = str(entity.get("type") or "").strip()
        entity_value = str(entity.get("value") or "").strip()
        if entity_type and entity_value:
            entities.append({"type": entity_type, "value": entity_value})

    planner_scope = detect_planner_scope(original_text, entities)
    if category in {"daily_planner", "weekly_planner"} and planner_scope and planner_scope != category:
        category = planner_scope
        confidence = max(confidence, 0.8 if category == "daily_planner" else 0.84)

    if category not in {"daily_planner", "weekly_planner"} and planner_scope:
        bullet_count = sum(
            1
            for line in (original_text or "").splitlines()
            if line.strip().startswith(("→", "-", "*", "•"))
        )
        if bullet_count >= 3:
            category = planner_scope
            confidence = max(confidence, 0.76)

    capture_intent, intent_confidence = infer_capture_intent(
        original_text,
        category=category,
        suggested_intent=parsed.get("capture_intent"),
    )
    validation = validate_capture(
        text=original_text,
        category=category,
        entities=entities,
        content_type=content_type,
    )

    return {
        "category": category,
        "confidence": confidence,
        "capture_intent": capture_intent,
        "intent_confidence": float(parsed.get("intent_confidence") or intent_confidence),
        "entities": entities,
        "tags": normalize_tags(parsed.get("tags") or []),
        "priority": str(parsed.get("priority") or "medium").lower(),
        "suggested_action": parsed.get("suggested_action"),
        "summary": str(parsed.get("summary") or original_text[:200]).strip(),
        "validation_status": validation["validation_status"],
        "quality_issues": validation["quality_issues"],
        "eligible_for_boards": validation["eligible_for_boards"],
        "eligible_for_project_state": validation["eligible_for_project_state"],
    }


def _fallback_classification(text: str) -> dict:
    dates = extract_planner_dates(text)
    bullet_count = sum(
        1
        for line in (text or "").splitlines()
        if line.strip().startswith(("→", "-", "*", "•"))
    )
    if dates and (bullet_count >= 3 or len(dates) >= 2):
        category = "weekly_planner" if len(dates) >= 3 else "daily_planner"
        scope = "week" if category == "weekly_planner" else "day"
        return {
            "category": category,
            "confidence": 0.82 if category == "weekly_planner" else 0.78,
            "entities": [{"type": "date", "value": item["label"]} for item in dates[:10]],
            "tags": ["planner", "classifier-fallback"],
            "priority": "medium",
            "suggested_action": f"Store this as a {scope} planner and surface the top items.",
            "summary": f"Planner capture covering {len(dates)} date entries.",
        }

    lowered = (text or "").lower()
    if re.search(r"\b(todo|follow up|apply to|interview prep|need to)\b", lowered):
        category = "task"
        confidence = 0.58
    elif re.search(r"\b(project|prototype|launch|website|build|integration)\b", lowered):
        category = "project"
        confidence = 0.55
    else:
        category = "note"
        confidence = 0.4

    return {
        "category": category,
        "confidence": confidence,
        "entities": [],
        "tags": ["classifier-fallback"],
        "priority": "medium",
        "suggested_action": "Review this capture and confirm the category if needed.",
        "summary": (text or "Unstructured capture").strip()[:200],
    }
