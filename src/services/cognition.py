"""Continuous synapse, critique, and learning loops."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.base import agent_call
from src.config import settings
from src.lib.llm_json import LLMJSONError, parse_json_object
from src.lib import store
from src.services.project_state import recompute_project_states
from src.services.story import publish_story_entry

COGNITION_SYSTEM_PROMPT = """You create grounded cognitive outputs for Ahmad's brain.

Return ONLY valid JSON with this exact shape:
{
  "synapses": [
    {"title": "connection title", "body": "why these projects connect", "source_refs": ["project"] }
  ],
  "blind_spots": [
    {"title": "blind spot", "body": "what may be missing", "project_ref": "project or null"}
  ],
  "research_threads": [
    {"title": "research thread", "body": "what to learn next", "project_ref": "project or null"}
  ]
}

Rules:
- Stay grounded in the supplied context only.
- Keep each item concise and actionable.
- Prefer concrete cross-project links and hidden risks over generic advice.
"""


async def run_continuous_cognition(
    session: AsyncSession,
    *,
    limit_projects: int = 4,
    trace_id: uuid.UUID | None = None,
) -> dict:
    await recompute_project_states(session)
    snapshots = await store.list_project_state_snapshots(session, limit=limit_projects)
    if not snapshots:
        return {"status": "completed", "items_imported": 0}

    projects = []
    for snapshot in snapshots:
        project = await store.get_note(session, snapshot.project_note_id)
        if project:
            projects.append((project, snapshot))

    context_lines = []
    for project, snapshot in projects[:limit_projects]:
        context_lines.extend(
            [
                f"Project: {project.title}",
                f"Implemented: {snapshot.implemented or 'unknown'}",
                f"Remaining: {snapshot.remaining or 'unknown'}",
                f"Holes: {', '.join(snapshot.holes or []) or 'none'}",
                f"What changed: {snapshot.what_changed or 'unknown'}",
                "",
            ]
        )
    context_text = "\n".join(context_lines).strip()

    synthesized = {"synapses": [], "blind_spots": [], "research_threads": []}
    try:
        result = await agent_call(
            session,
            agent_name="continuous_cognition",
            action="run",
            prompt=context_text,
            system=COGNITION_SYSTEM_PROMPT,
            model=settings.sonnet_model,
            max_tokens=1600,
            temperature=0.2,
            trace_id=trace_id,
        )
        synthesized = parse_json_object(result["text"])
    except (LLMJSONError, Exception):
        pass

    if not synthesized["blind_spots"]:
        synthesized["blind_spots"] = [
            {
                "title": f"Evidence gap: {project.title}",
                "body": snapshot.remaining or "Need stronger proof of what remains and what is blocked.",
                "project_ref": project.title,
            }
            for project, snapshot in projects[:2]
        ]
    if not synthesized["synapses"] and len(projects) >= 2:
        left, _ = projects[0]
        right, _ = projects[1]
        synthesized["synapses"] = [
            {
                "title": f"{left.title} x {right.title}",
                "body": f"Both projects appear active and may share constraints or reusable approaches.",
                "source_refs": [left.title, right.title],
            }
        ]
    if not synthesized["research_threads"]:
        synthesized["research_threads"] = [
            {
                "title": f"Research next step for {project.title}",
                "body": snapshot.remaining or "Clarify the highest-uncertainty next step.",
                "project_ref": project.title,
            }
            for project, snapshot in projects[:1]
        ]

    created = 0
    for synapse in synthesized.get("synapses") or []:
        await publish_story_entry(
            session,
            actor_type="system",
            actor_name="cognition-loop",
            subject_type="topic",
            subject_ref=" / ".join(list(synapse.get("source_refs") or [])[:2]) or None,
            entry_type="synapse",
            title=synapse.get("title") or "Cross-project synapse",
            body_markdown=synapse.get("body") or "",
            summary=(synapse.get("body") or "")[:240],
            impact="The brain found a non-obvious connection worth carrying forward.",
            source="agent",
            category="note",
            tags=["synapse", "continuous-cognition"],
        )
        created += 1
    for blind_spot in synthesized.get("blind_spots") or []:
        await publish_story_entry(
            session,
            actor_type="system",
            actor_name="cognition-loop",
            subject_type="project" if blind_spot.get("project_ref") else "topic",
            subject_ref=blind_spot.get("project_ref"),
            entry_type="blind_spot",
            title=blind_spot.get("title") or "Blind spot",
            body_markdown=blind_spot.get("body") or "",
            summary=(blind_spot.get("body") or "")[:240],
            impact="The brain flagged a missing angle or weak evidence area.",
            source="agent",
            category="note",
            tags=["blind-spot", "continuous-cognition"],
        )
        created += 1
    for thread in synthesized.get("research_threads") or []:
        await publish_story_entry(
            session,
            actor_type="system",
            actor_name="cognition-loop",
            subject_type="project" if thread.get("project_ref") else "topic",
            subject_ref=thread.get("project_ref"),
            entry_type="research_thread",
            title=thread.get("title") or "Research thread",
            body_markdown=thread.get("body") or "",
            summary=(thread.get("body") or "")[:240],
            open_question=(thread.get("body") or "")[:240],
            source="agent",
            category="note",
            tags=["research-thread", "continuous-cognition"],
        )
        created += 1

    return {"status": "completed", "items_imported": created}
