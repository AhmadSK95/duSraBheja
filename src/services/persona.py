"""Structured persona packet for Ahmad-facing narration and agent handoffs."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.lib import store
from src.services.brain_atlas import build_brain_atlas_snapshot


def _top_titles(items: list[dict[str, Any]], *, limit: int = 4) -> list[str]:
    titles: list[str] = []
    seen: set[str] = set()
    for item in items:
        title = str(item.get("title") or "").strip()
        lowered = title.lower()
        if not title or lowered in seen:
            continue
        seen.add(lowered)
        titles.append(title)
        if len(titles) >= limit:
            break
    return titles


def _current_headspace_titles(snapshot: dict[str, Any], *, limit: int = 5) -> list[str]:
    return _top_titles(list(snapshot.get("current_headspace") or []), limit=limit)


def _facet_titles(snapshot: dict[str, Any], facet_type: str, *, limit: int = 4) -> list[str]:
    matching = [
        item
        for item in list(snapshot.get("facets") or [])
        if str(item.get("facet_type") or "") == facet_type
    ]
    return _top_titles(matching, limit=limit)


def _fallback_packet(snapshot: dict[str, Any]) -> dict[str, Any]:
    current = _current_headspace_titles(snapshot)
    interests = _facet_titles(snapshot, "interests")
    media = _facet_titles(snapshot, "media")
    projects = _facet_titles(snapshot, "projects")
    return {
        "summary": "Direct, analytical, evidence-led builder voice with low fluff and a strong preference for clarity over performance.",
        "tone_contract": [
            "Sound like Ahmad talking to himself after thinking clearly, not like a support bot.",
            "Lead with the answer in natural prose, then show the evidence path.",
            "Use numbers, dates, and counts when they sharpen the point.",
            "Be honest when the evidence is mixed or thin.",
            "Keep taste and judgment visible, not sterilized.",
        ],
        "evidence_preferences": {
            "style": "direct answer first, evidence second, inference labeled",
            "numbers": "use counts, dates, weights, and deltas when they genuinely help",
            "certainty_language": "avoid confidence-score language in the user-facing answer",
        },
        "current_headspace": current,
        "active_projects": projects,
        "taste_signals": {
            "interests": interests,
            "media": media,
        },
        "style_anchors": [],
    }


async def build_persona_packet(
    session: AsyncSession,
    *,
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot_payload = snapshot or {}
    if not snapshot_payload:
        try:
            snapshot_payload = (await build_brain_atlas_snapshot(session)).as_dict()
        except Exception:
            snapshot_payload = {}

    fallback = _fallback_packet(snapshot_payload)

    try:
        voice_profile = await store.get_voice_profile(session, "ahmad-default")
    except Exception:
        voice_profile = None

    if not voice_profile:
        return fallback

    traits = dict(voice_profile.traits or {})
    tone_traits = [str(item) for item in list(traits.get("tone") or [])[:4]]
    priorities = [str(item) for item in list(traits.get("priorities") or [])[:4]]
    patterns = [str(item) for item in list(traits.get("patterns") or [])[:5]]

    return {
        "summary": str(voice_profile.summary or fallback["summary"]),
        "tone_contract": [
            *(fallback["tone_contract"] or []),
            *(f"Lean into this tone: {item}" for item in tone_traits[:3]),
        ][:7],
        "evidence_preferences": {
            "style": "answer first, evidence second, inference labeled",
            "numbers": "Ahmad likes numbers when they sharpen the point, not when they create false precision",
            "certainty_language": "state strength of evidence plainly instead of using robotic confidence tags",
            "priorities": priorities,
            "patterns": patterns,
        },
        "current_headspace": _current_headspace_titles(snapshot_payload),
        "active_projects": _facet_titles(snapshot_payload, "projects"),
        "taste_signals": {
            "interests": _facet_titles(snapshot_payload, "interests"),
            "media": _facet_titles(snapshot_payload, "media"),
        },
        "style_anchors": list(voice_profile.style_anchors or [])[:3],
    }


def render_persona_context(packet: dict[str, Any] | None) -> str:
    payload = packet or {}
    lines = [
        f"Summary: {payload.get('summary') or 'No persona summary available.'}",
    ]
    tone_contract = list(payload.get("tone_contract") or [])
    if tone_contract:
        lines.append("Tone contract:")
        lines.extend(f"- {item}" for item in tone_contract[:6])

    evidence_preferences = dict(payload.get("evidence_preferences") or {})
    if evidence_preferences:
        lines.append("Evidence preferences:")
        for key in ("style", "numbers", "certainty_language"):
            value = evidence_preferences.get(key)
            if value:
                lines.append(f"- {key}: {value}")
        priorities = list(evidence_preferences.get("priorities") or [])
        if priorities:
            lines.append(f"- priorities: {', '.join(priorities[:4])}")
        patterns = list(evidence_preferences.get("patterns") or [])
        if patterns:
            lines.append(f"- patterns: {', '.join(patterns[:5])}")

    current_headspace = list(payload.get("current_headspace") or [])
    if current_headspace:
        lines.append(f"Current headspace: {', '.join(current_headspace[:5])}")

    active_projects = list(payload.get("active_projects") or [])
    if active_projects:
        lines.append(f"Active projects: {', '.join(active_projects[:4])}")

    taste_signals = dict(payload.get("taste_signals") or {})
    interest_titles = list(taste_signals.get("interests") or [])
    media_titles = list(taste_signals.get("media") or [])
    if interest_titles:
        lines.append(f"Interest signals: {', '.join(interest_titles[:4])}")
    if media_titles:
        lines.append(f"Media signals: {', '.join(media_titles[:4])}")

    style_anchors = list(payload.get("style_anchors") or [])
    if style_anchors:
        lines.append("Style anchors:")
        for anchor in style_anchors[:2]:
            label = str(anchor.get("label") or "anchor")
            why = str(anchor.get("why") or "useful signal")
            sample = str(anchor.get("sample") or "").strip()
            lines.append(f"- {label}: {why}")
            if sample:
                lines.append(f"  sample: {sample[:140]}")

    return "\n".join(lines).strip()
