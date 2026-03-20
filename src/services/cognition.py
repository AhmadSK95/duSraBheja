"""Continuous synapse, critique, and learning loops."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.base import agent_call
from src.config import settings
from src.lib import store
from src.lib.llm_json import LLMJSONError, parse_json_object
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
  ],
  "patterns": [
    {"title": "pattern name", "body": "cross-cutting theme or repeated approach", "source_refs": ["project"]}
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

    synthesized = {"synapses": [], "blind_spots": [], "research_threads": [], "patterns": []}
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
                "body": snapshot.remaining or "Need stronger proof of what remains.",
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
                "body": "Both active — may share constraints or reusable approaches.",
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

    for pattern in synthesized.get("patterns") or []:
        await store.upsert_synthesis_record(
            session,
            source_kind="cognition",
            source_ref=f"pattern:{pattern.get('title', 'unknown')}",
            project_note_id=None,
            synthesis_type="pattern",
            title=pattern.get("title") or "Cross-cutting pattern",
            summary=(pattern.get("body") or "")[:500],
            body=pattern.get("body") or "",
            certainty_class="plausible_inference",
            provenance_kind="derived_system",
            metadata_={"source_refs": list(pattern.get("source_refs") or [])},
        )
        created += 1

    # Phase 10: Expertise model synthesis
    expertise_created = await _synthesize_expertise_models(session, projects, trace_id=trace_id)
    created += expertise_created

    return {"status": "completed", "items_imported": created}


EXPERTISE_SYSTEM_PROMPT = """\
You create a deep expertise model for a project domain.

Not just "uses Python" — capture how Ahmad approaches this domain:
- Architectural patterns and preferences
- Decision-making heuristics
- Lessons from failure
- What he'd do differently
- Cross-domain connections

Return ONLY valid JSON:
{
  "domain": "the domain name",
  "approach": "how Ahmad approaches systems in this domain",
  "patterns": ["specific patterns he gravitates toward"],
  "heuristics": ["decision rules he applies"],
  "anti_patterns": ["things he avoids and why"],
  "cross_domain": ["connections to other domains"],
  "depth_signals": ["evidence of genuine depth, not surface"]
}

Stay grounded in the evidence. If a section is thin, say so.
"""


async def _synthesize_expertise_models(
    session: AsyncSession,
    projects: list[tuple],
    *,
    trace_id: uuid.UUID | None = None,
) -> int:
    """For each active project domain, generate an expertise_model SynthesisRecord."""
    created = 0
    for project, snapshot in projects[:3]:
        source_ref = f"expertise:{project.title}"

        # Check staleness — skip if updated within 7 days
        existing = await store.list_synthesis_records(
            session, synthesis_type="expertise_model", q=project.title, limit=1
        )
        if existing:
            from datetime import datetime, timedelta, timezone

            now = datetime.now(timezone.utc)
            last = existing[0].updated_at or existing[0].created_at
            if last and (now - last) < timedelta(days=7):
                continue

        context = (
            f"Project: {project.title}\n"
            f"Implemented: {snapshot.implemented or 'unknown'}\n"
            f"Remaining: {snapshot.remaining or 'unknown'}\n"
            f"Holes: {', '.join(snapshot.holes or []) or 'none'}\n"
            f"Risks: {', '.join(snapshot.risks or []) or 'none'}\n"
            f"What changed: {snapshot.what_changed or 'unknown'}"
        )

        try:
            result = await agent_call(
                session,
                agent_name="continuous_cognition",
                action="expertise_model",
                prompt=context,
                system=EXPERTISE_SYSTEM_PROMPT,
                model=settings.sonnet_model,
                max_tokens=1600,
                temperature=0.2,
                trace_id=trace_id,
            )
            expertise = parse_json_object(result["text"])
        except (LLMJSONError, Exception):
            continue

        await store.upsert_synthesis_record(
            session,
            source_kind="cognition",
            source_ref=source_ref,
            project_note_id=project.id,
            synthesis_type="expertise_model",
            title=f"Expertise: {expertise.get('domain', project.title)}",
            summary=expertise.get("approach", "")[:500],
            body=(
                expertise.get("approach", "")
                + "\n\n"
                + "\n".join(f"- {p}" for p in expertise.get("patterns", []))
            ),
            certainty_class="grounded_observation",
            metadata_=expertise,
        )
        created += 1

    return created
