"""Optional live web search enrichments via OpenAI Responses."""

from __future__ import annotations

import json

import openai

from src.config import settings
from src.lib.llm_json import parse_json_object, LLMJSONError

client = openai.AsyncOpenAI(api_key=settings.openai_api_key)


async def search_youtube_learning_queries(*, topics: list[str]) -> list[dict]:
    if not settings.openai_api_key or not topics:
        return []

    prompt = (
        "Return ONLY valid JSON with this exact shape: "
        '{"items":[{"title":"short title","url":"https://youtube.com/... or null","search_query":"youtube search query",'
        '"why":"why it helps Ahmad now"}]}. '
        "Use live web search and prefer grounded direct YouTube video, playlist, or channel URLs when confidently found. "
        "If you cannot ground a direct URL, set url to null and provide a strong search_query instead. "
        f"Topics: {', '.join(topics[:8])}"
    )
    try:
        response = await client.responses.create(
            model=settings.openai_web_search_model,
            tools=[{"type": "web_search_preview"}],
            input=prompt,
        )
        payload = parse_json_object(response.output_text)
        return list(payload.get("items") or [])[:5]
    except (openai.OpenAIError, LLMJSONError, json.JSONDecodeError, AttributeError):
        return []


async def search_brain_teasers_with_web(*, topics: list[str]) -> list[dict]:
    if not settings.openai_api_key or not topics:
        return []

    prompt = (
        "Return ONLY valid JSON with this exact shape: "
        '{"items":[{"title":"short title","prompt":"thoughtful teaser or puzzle",'
        '"hint":"small hint","url":"https://example.com or null","why":"why it fits Ahmad now"}]}. '
        "Use live web search when helpful. Prefer real puzzle, article, or challenge links when grounded; otherwise set url to null. "
        "Make the teasers smart, practical, and connected to systems thinking, product thinking, software delivery, or the active topics. "
        f"Topics: {', '.join(topics[:8])}"
    )
    try:
        response = await client.responses.create(
            model=settings.openai_web_search_model,
            tools=[{"type": "web_search_preview"}],
            input=prompt,
        )
        payload = parse_json_object(response.output_text)
        return list(payload.get("items") or [])[:5]
    except (openai.OpenAIError, LLMJSONError, json.JSONDecodeError, AttributeError):
        return []


async def research_topic_brief(*, topic: str, questions: list[str]) -> dict | None:
    if not settings.openai_api_key or not topic:
        return None

    prompt = (
        "Return ONLY valid JSON with this exact shape: "
        '{"summary":"short summary","findings":[{"title":"finding","detail":"detail","source_hint":"source hint"}],'
        '"followups":["question"]}. '
        "Use live web search to ground the response in current information when needed. "
        f"Topic: {topic}. Questions: {' | '.join(questions[:6])}"
    )
    try:
        response = await client.responses.create(
            model=settings.openai_web_search_model,
            tools=[{"type": "web_search_preview"}],
            input=prompt,
        )
        return parse_json_object(response.output_text)
    except (openai.OpenAIError, LLMJSONError, json.JSONDecodeError, AttributeError):
        return None


async def answer_question_with_web(*, question: str, context_hints: list[str] | None = None) -> dict | None:
    if not settings.openai_api_key or not question:
        return None

    hint_text = " | ".join(item for item in (context_hints or []) if item)
    prompt = (
        "Return ONLY valid JSON with this exact shape: "
        '{"answer":"grounded answer","sources":[{"title":"title","url":"https://example.com","source_hint":"why it matters"}]}. '
        "Use live web search. Keep the answer concise and factual. "
        f"Question: {question}. Context hints: {hint_text}"
    )
    try:
        response = await client.responses.create(
            model=settings.openai_web_search_model,
            tools=[{"type": "web_search_preview"}],
            input=prompt,
        )
        return parse_json_object(response.output_text)
    except (openai.OpenAIError, LLMJSONError, json.JSONDecodeError, AttributeError):
        return None
