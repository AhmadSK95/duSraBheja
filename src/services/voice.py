"""Ahmad voice-profile synthesis from personal sources."""

from __future__ import annotations

import uuid
from collections import Counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.base import agent_call
from src.config import settings
from src.lib.llm_json import LLMJSONError, parse_json_object
from src.lib import store
from src.models import SourceItem, SyncSource

VOICE_PROFILE_SYSTEM_PROMPT = """You build a durable voice profile for Ahmad from personal writing.

Return ONLY valid JSON with this exact shape:
{
  "summary": "short voice summary",
  "traits": {
    "tone": ["trait"],
    "priorities": ["priority"],
    "patterns": ["pattern"]
  },
  "style_anchors": [
    {"label": "anchor", "sample": "short safe sample", "why": "why this matters"}
  ]
}

Rules:
- Keep samples short and safe.
- Focus on writing behavior, priorities, and stylistic tendencies.
- Do not include secrets or sensitive details.
"""


def _fallback_profile(snippets: list[dict]) -> dict:
    token_counter = Counter()
    for item in snippets:
        words = [word.strip(".,:;!?()[]{}").lower() for word in item.get("text", "").split()]
        token_counter.update(word for word in words if len(word) > 4)

    common = [word for word, _count in token_counter.most_common(8)]
    return {
        "summary": "Direct, thoughtful, low-fluff builder-operator voice with recurring focus on execution and clarity.",
        "traits": {
            "tone": ["direct", "thoughtful", "low-fluff"],
            "priorities": ["clarity", "progress", "execution"],
            "patterns": common[:5],
        },
        "style_anchors": [
            {
                "label": item.get("label") or "personal-writing",
                "sample": (item.get("text") or "")[:160],
                "why": "Recent personal writing sample",
            }
            for item in snippets[:3]
            if item.get("text")
        ],
    }


async def refresh_voice_profile(
    session: AsyncSession,
    *,
    profile_name: str = "ahmad-default",
    limit: int = 12,
    trace_id: uuid.UUID | None = None,
) -> dict:
    source_snippets: list[dict] = []

    recent_activity = await store.list_recent_activity(session, limit=30)
    for entry in recent_activity:
        if entry.actor_name not in {"discord", "manual", "command", "mcp", "ask-brain"}:
            continue
        if not (entry.body_markdown or entry.summary):
            continue
        source_snippets.append(
            {
                "label": f"journal:{entry.entry_type}",
                "text": (entry.body_markdown or entry.summary or "")[:800],
                "source_ref": str(entry.id),
            }
        )
        if len(source_snippets) >= limit:
            break

    source_items = await session.execute(
        select(SourceItem, SyncSource.source_type)
        .join(SyncSource, SourceItem.sync_source_id == SyncSource.id)
        .where(SyncSource.source_type.in_(("gmail", "google_keep", "apple_notes")))
        .order_by(SourceItem.happened_at.desc().nullslast(), SourceItem.created_at.desc())
        .limit(limit * 2)
    )
    for item, source_type in source_items.all():
        if len(source_snippets) >= limit:
            break
        text = (item.summary or "")[:800]
        if not text:
            continue
        source_snippets.append(
            {
                "label": f"{source_type}:{item.title}" if source_type else item.title,
                "text": text,
                "source_ref": str(item.id),
            }
        )

    profile_payload = _fallback_profile(source_snippets)
    if source_snippets:
        prompt = "\n\n".join(
            f"[{item['label']}] {item['text']}"
            for item in source_snippets[:limit]
            if item.get("text")
        )
        try:
            result = await agent_call(
                session,
                agent_name="voice_profiler",
                action="refresh_voice_profile",
                prompt=prompt,
                system=VOICE_PROFILE_SYSTEM_PROMPT,
                model=settings.sonnet_model,
                max_tokens=1200,
                temperature=0.1,
                trace_id=trace_id,
            )
            profile_payload = parse_json_object(result["text"])
        except (LLMJSONError, Exception):
            pass

    profile = await store.upsert_voice_profile(
        session,
        profile_name=profile_name,
        summary=str(profile_payload.get("summary") or _fallback_profile(source_snippets)["summary"]),
        traits=dict(profile_payload.get("traits") or {}),
        style_anchors=list(profile_payload.get("style_anchors") or []),
        source_refs=[
            {"source_ref": item.get("source_ref"), "label": item.get("label")}
            for item in source_snippets[:limit]
        ],
        metadata_={"source_count": len(source_snippets)},
    )
    return {
        "profile_name": profile.profile_name,
        "summary": profile.summary,
        "traits": profile.traits,
        "style_anchors": profile.style_anchors,
    }
