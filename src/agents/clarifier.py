"""Clarifier agent — generates follow-up questions for low-confidence classifications."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.base import agent_call
from src.config import settings

SYSTEM_PROMPT = """You are a clarification agent for a personal second brain.

When a piece of input is ambiguous and the classifier is unsure, you ask ONE short,
natural-language question to resolve the ambiguity.

Rules:
- Ask exactly ONE question
- Keep it under 30 words
- Make it answerable with a short phrase or yes/no
- Be friendly and casual (this is a personal system)
- Focus on the category ambiguity (what kind of thing is this?)
- Don't ask about details that aren't needed for classification"""


async def generate_question(
    session: AsyncSession,
    original_text: str,
    classification_attempt: dict,
    trace_id: uuid.UUID | None = None,
) -> str:
    """Generate a clarification question for a low-confidence classification.

    Returns the question as a string.
    """
    prompt = f"""The user sent this:
"{original_text}"

The classifier's best guess:
- Category: {classification_attempt.get("category", "unknown")}
- Confidence: {classification_attempt.get("confidence", 0)}
- Summary: {classification_attempt.get("summary", "")}

Generate ONE short clarification question to help classify this correctly."""

    result = await agent_call(
        session,
        agent_name="clarifier",
        action="generate_question",
        prompt=prompt,
        system=SYSTEM_PROMPT,
        model=settings.sonnet_model,
        max_tokens=256,
        temperature=0.3,
        trace_id=trace_id,
    )

    return result["text"].strip()
