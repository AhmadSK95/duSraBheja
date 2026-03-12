"""Retriever agent — hybrid project-aware RAG with citations."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.services.query import candidate_lookup_phrases, query_brain


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


def _candidate_lookup_phrases(question: str) -> list[str]:
    return candidate_lookup_phrases(question)

async def answer_question(
    session: AsyncSession,
    question: str,
    category: str | None = None,
    use_opus: bool = False,
    trace_id: uuid.UUID | None = None,
) -> dict:
    """Route freeform questions through the story-aware query service."""
    _ = trace_id or uuid.uuid4()
    return await query_brain(
        session,
        question=question,
        category=category,
        use_opus=use_opus,
    )
