"""Daily digest assembly from stored notes, story events, and grounded synthesis."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from src.agents.storyteller import compose_digest_sections
from src.config import settings
from src.services.openai_web import search_youtube_learning_queries
from src.services.project_state import recompute_project_states

try:
    from src.lib import store
except ModuleNotFoundError:  # pragma: no cover - allows unit tests without app deps
    store = None

log = logging.getLogger("brain.digest")
LOW_SIGNAL_PROJECT_ENTRY_TYPES = {"context_dump", "repo_snapshot"}
INDIRECT_PROJECT_ENTRY_TYPES = {"knowledge_refresh", "voice_refresh"}


def _shorten(value: str | None, limit: int = 220) -> str:
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1].rstrip()}…"


def _default_digest_sections() -> dict:
    return {
        "headline": True,
        "top_active_projects": True,
        "changed_since_yesterday": True,
        "project_status": True,
        "recommended_tasks": True,
        "new_synapses": True,
        "brain_learning": True,
        "blind_spots": True,
        "voice_alignment": True,
        "youtube_recommendations": True,
        "brain_teasers": True,
        "reminders_due_today": True,
        "improvement_focus": True,
    }


def _fallback_brain_teasers(digest_date: date) -> list[dict]:
    pool = [
        {
            "title": "Dependency Knot",
            "prompt": "You have three services that each depend on a different one starting first. What is the smallest change that makes the system bootable?",
            "hint": "Break the circular dependency with one deferred edge.",
        },
        {
            "title": "Commit Sequence",
            "prompt": "A feature took 5 commits, but only 2 matter to the final behavior. How would you identify the minimum explanatory sequence?",
            "hint": "Think in terms of behavior-changing commits, not formatting commits.",
        },
        {
            "title": "Planner Compression",
            "prompt": "If 12 tasks collapse into 3 themes, what signal tells you the right grouping is not arbitrary?",
            "hint": "Look for shared constraint or outcome, not shared wording.",
        },
        {
            "title": "Narrative Gap",
            "prompt": "A project looks active, but there is no evidence of progress for 6 days. What are the three most plausible explanations?",
            "hint": "Consider silence, hidden work, and blocked work.",
        },
        {
            "title": "Search Budget",
            "prompt": "If you can only retrieve 5 pieces of evidence for a project answer, which types would you choose first and why?",
            "hint": "Prioritize recency, directness, and decision relevance.",
        },
        {
            "title": "Risk Lens",
            "prompt": "A system works in demos but not in ops. What missing story signal would most likely explain the gap?",
            "hint": "Think about deployment and failure handling.",
        },
    ]
    offset = digest_date.toordinal() % len(pool)
    return [pool[(offset + index) % len(pool)] for index in range(5)]


def _fallback_task_recommendations(tasks: list, open_loops: list[dict], projects: list) -> list[dict]:
    recommendations: list[dict] = []
    for task in tasks[:10]:
        recommendations.append(
            {
                "title": task.title,
                "why": f"Already active in the brain with priority {getattr(task, 'priority', 'medium')}.",
                "project_ref": None,
            }
        )
    for loop in open_loops:
        question = loop.get("open_question")
        if not question:
            continue
        recommendations.append(
            {
                "title": f"Resolve: {_shorten(question, 90)}",
                "why": "This is an unresolved loop surfaced by recent activity.",
                "project_ref": None,
            }
        )
        if len(recommendations) >= 10:
            break
    for project in projects:
        if len(recommendations) >= 10:
            break
        recommendations.append(
            {
                "title": f"Push {project.title} forward",
                "why": "It is one of the currently active projects in the brain.",
                "project_ref": project.title,
            }
        )
    return recommendations[:10]


def _fallback_project_assessments(projects: list, project_updates: dict[str, list[dict]]) -> list[dict]:
    assessments = []
    for project in projects[:5]:
        updates = project_updates.get(str(project.id), [])
        recent_titles = ", ".join(item["title"] for item in updates[:2]) or "Signals are thin."
        open_questions = [item.get("open_question") for item in updates if item.get("open_question")]
        assessments.append(
            {
                "project": project.title,
                "where_it_stands": _shorten(
                    getattr(project, "content", None)
                    or getattr(project, "status", None)
                    or "Active project with limited canonical summary."
                ),
                "implemented": _shorten(recent_titles),
                "left": _shorten(open_questions[0] if open_questions else "Need clearer evidence of remaining work."),
                "holes": "Story coverage is still incomplete for this project." if not updates else "Need stronger completion and risk signals.",
                "next_step": f"Review the latest story events and tighten the next concrete deliverable for {project.title}.",
            }
        )
    return assessments


def _fallback_video_recommendations(projects: list, story_connections: list[dict]) -> list[dict]:
    subjects = [project.title for project in projects[:5]]
    subjects.extend(
        item["subject_ref"]
        for item in story_connections[:5]
        if item.get("subject_ref") and item["subject_ref"] not in subjects
    )
    while len(subjects) < 5:
        subjects.append("systems thinking")
    recommendations = []
    for subject in subjects[:5]:
        recommendations.append(
            {
                "title": f"{subject} deep dive",
                "search_query": f"{subject} walkthrough architecture tutorial",
                "why": "Grounded in current projects and recurring story connections.",
            }
        )
    return recommendations


def _fallback_writing_topics(projects: list, open_loops: list[dict]) -> list[dict]:
    topics = [{"title": project.title, "why": "Active project with fresh story signals."} for project in projects[:5]]
    for loop in open_loops:
        question = loop.get("open_question")
        if not question:
            continue
        topics.append(
            {
                "title": _shorten(question, 80),
                "why": "This unresolved question could become a reflective writing angle.",
            }
        )
        if len(topics) >= 5:
            break
    return topics[:5]


def _normalize_topic_items(items: list[dict] | None, fallback: list[dict], *, title_key: str = "title") -> tuple[list[dict], list[str]]:
    normalized: list[dict] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        title = _shorten(str(item.get(title_key) or "").strip(), 140)
        if not title:
            continue
        cleaned = {key: _shorten(str(value), 220) if isinstance(value, str) else value for key, value in item.items()}
        cleaned[title_key] = title
        normalized.append(cleaned)
    if not normalized:
        normalized = fallback
    titles = [item[title_key] for item in normalized if item.get(title_key)]
    return normalized, titles


def _story_signal_item(entry) -> dict:
    return {
        "id": str(entry.id),
        "title": entry.title,
        "summary": _shorten(getattr(entry, "summary", None) or getattr(entry, "body_markdown", None), 220),
        "project_ref": getattr(entry, "subject_ref", None),
        "happened_at": str(entry.happened_at),
    }


def _is_meaningful_project_update(entry) -> bool:
    entry_type = getattr(entry, "entry_type", "") or ""
    actor_type = getattr(entry, "actor_type", "") or ""
    if entry_type in INDIRECT_PROJECT_ENTRY_TYPES and actor_type in {"connector", "system"}:
        return False
    if entry_type in {"conversation_session", "session_closeout", "progress_update", "decision", "research_thread", "synapse", "blind_spot"}:
        return True
    if getattr(entry, "decision", None) or getattr(entry, "impact", None) or getattr(entry, "outcome", None) or getattr(entry, "open_question", None):
        return True
    if actor_type == "agent":
        return True
    if actor_type == "connector" and entry_type in LOW_SIGNAL_PROJECT_ENTRY_TYPES:
        return False
    return entry_type not in LOW_SIGNAL_PROJECT_ENTRY_TYPES


def _build_digest_context(
    *,
    digest_date: date,
    trigger: str,
    tasks: list,
    projects: list,
    project_snapshots: dict[str, object],
    resources: list,
    recent_activity: list,
    pending_reviews: list,
    open_loops: list[dict],
    story_connections: list[dict],
    project_updates: dict[str, list[dict]],
    reminders_due_today: list,
    synapses: list[dict],
    brain_learnings: list[dict],
    blind_spots: list[dict],
    voice_profile: dict | None,
) -> str:
    lines = [
        f"Digest date: {digest_date.isoformat()}",
        f"Trigger: {trigger}",
        "",
        "Tasks:",
    ]
    lines.extend(
        f"- {task.title} | priority={getattr(task, 'priority', 'medium')} | status={getattr(task, 'status', 'active')}"
        for task in tasks[:10]
    )
    lines.extend(["", "Projects:"])
    for project in projects[:6]:
        snapshot = project_snapshots.get(str(project.id))
        lines.append(
            "- "
            f"{project.title} | status={getattr(snapshot, 'status', None) or getattr(project, 'status', 'active')} | "
            f"summary={_shorten(getattr(project, 'content', None), 240) or 'none'}"
        )
        if snapshot:
            lines.append(
                "  - "
                f"active_score={getattr(snapshot, 'active_score', 0):.2f} | "
                f"implemented={_shorten(getattr(snapshot, 'implemented', None), 180) or 'unknown'} | "
                f"remaining={_shorten(getattr(snapshot, 'remaining', None), 180) or 'unknown'} | "
                f"holes={_shorten(', '.join(getattr(snapshot, 'holes', []) or []), 180) or 'none'}"
            )
        for update in project_updates.get(str(project.id), [])[:3]:
            lines.append(
                "  - "
                f"{update['title']} | decision={update.get('decision') or 'none'} | "
                f"impact={update.get('impact') or 'none'} | open_question={update.get('open_question') or 'none'}"
            )
    if resources:
        lines.extend(["", "Resources:"])
        lines.extend(
            f"- {resource.title}: {_shorten(getattr(resource, 'content', None), 180) or 'no summary'}"
            for resource in resources[:5]
        )
    if recent_activity:
        lines.extend(["", "Recent Activity:"])
        for entry in recent_activity[:8]:
            lines.append(
                " - "
                f"{entry.title} | actor={entry.actor_name} | type={entry.entry_type} | "
                f"decision={getattr(entry, 'decision', None) or 'none'} | "
                f"impact={getattr(entry, 'impact', None) or 'none'} | "
                f"open_question={getattr(entry, 'open_question', None) or 'none'}"
            )
    if pending_reviews:
        lines.extend(["", "Pending Reviews:"])
        lines.extend(f"- {review.question}" for review in pending_reviews[:5])
    if reminders_due_today:
        lines.extend(["", "Reminders Due Today:"])
        for reminder in reminders_due_today[:8]:
            lines.append(
                f"- {reminder.title} | next_fire={reminder.next_fire_at.isoformat() if reminder.next_fire_at else 'none'}"
            )
    if open_loops:
        lines.extend(["", "Open Loops:"])
        lines.extend(f"- {item.get('open_question') or item.get('title')}" for item in open_loops[:6])
    if story_connections:
        lines.extend(["", "Cross-Project Connections:"])
        lines.extend(f"- {item['subject_ref']} ({item['mentions']} mentions)" for item in story_connections[:6])
    if synapses:
        lines.extend(["", "New Synapses:"])
        lines.extend(f"- {item['title']}: {item['summary'] or 'Fresh connection'}" for item in synapses[:6])
    if brain_learnings:
        lines.extend(["", "Brain Learning:"])
        lines.extend(f"- {item['title']}: {item['summary'] or 'Fresh learning signal'}" for item in brain_learnings[:6])
    if blind_spots:
        lines.extend(["", "Blind Spots:"])
        lines.extend(f"- {item['title']}: {item['summary'] or 'Evidence gap'}" for item in blind_spots[:6])
    if voice_profile:
        lines.extend(
            [
                "",
                "Voice Profile:",
                f"- Summary: {voice_profile.get('summary') or 'unknown'}",
                f"- Tone: {', '.join(voice_profile.get('traits', {}).get('tone') or []) or 'unknown'}",
                f"- Priorities: {', '.join(voice_profile.get('traits', {}).get('priorities') or []) or 'unknown'}",
            ]
        )
    lines.extend(
        [
            "",
            "Instructions:",
            "- Recommend strong tasks for today.",
            "- Assess active projects with what is implemented, what is left, and what looks weak.",
            "- Surface cross-project synapses, external learning, and blind spots that matter right now.",
            "- Suggest five writing topics.",
            "- Suggest five YouTube watch ideas that fit Ahmad's active domains and current project gaps.",
            "- Generate five smart brain teasers.",
            "- Include one or two improvement-focus suggestions based on where Ahmad should work next.",
        ]
    )
    return "\n".join(lines)


async def build_daily_digest_payload(session, *, digest_date: date, trigger: str = "scheduled") -> dict:
    if store is None:
        raise RuntimeError("store module is unavailable")

    await store.upsert_digest_preference(
        session,
        profile_name="default",
        timezone_name="America/New_York",
        sections=_default_digest_sections(),
        metadata_={},
    )
    digest_preference = await store.get_digest_preference(session, "default")

    await recompute_project_states(session)
    tasks = await store.list_notes(session, category="task", limit=10)
    resources = await store.list_notes(session, category="resource", limit=10)
    recent_activity = await store.list_recent_activity(session, limit=30)
    pending_reviews = await store.get_pending_reviews(session)
    reminders = await store.list_reminders(session, status="active", limit=50)
    snapshots = await store.list_project_state_snapshots(session, limit=25)
    get_voice_profile = getattr(store, "get_voice_profile", None)
    voice_profile_record = await get_voice_profile(session, "ahmad-default") if get_voice_profile else None

    recent_cutoff = digest_date - timedelta(days=7)
    project_updates: dict[str, list[dict]] = {}
    open_loops = []
    synapses: list[dict] = []
    brain_learnings: list[dict] = []
    blind_spots: list[dict] = []
    active_project_map: dict[str, object] = {}
    for entry in recent_activity:
        if entry.happened_at.date() < recent_cutoff:
            continue
        if entry.entry_type == "synapse":
            synapses.append(_story_signal_item(entry))
            continue
        if entry.entry_type in {"knowledge_refresh", "research_thread", "voice_refresh"}:
            brain_learnings.append(_story_signal_item(entry))
            continue
        if entry.entry_type == "blind_spot":
            blind_spots.append(_story_signal_item(entry))
            continue
        if not entry.project_note_id:
            if getattr(entry, "open_question", None):
                open_loops.append(
                    {
                        "title": entry.title,
                        "open_question": entry.open_question,
                        "happened_at": str(entry.happened_at),
                    }
                )
        else:
            if _is_meaningful_project_update(entry):
                project_updates.setdefault(str(entry.project_note_id), []).append(
                    {
                        "title": entry.title,
                        "entry_type": entry.entry_type,
                        "happened_at": str(entry.happened_at),
                        "decision": getattr(entry, "decision", None),
                        "impact": getattr(entry, "impact", None),
                        "open_question": getattr(entry, "open_question", None),
                    }
                )
            project = await store.get_note(session, entry.project_note_id)
            if project and _is_meaningful_project_update(entry):
                active_project_map[str(project.id)] = project

    snapshot_map = {str(snapshot.project_note_id): snapshot for snapshot in snapshots}
    projects = []
    for snapshot in snapshots:
        project = await store.get_note(session, snapshot.project_note_id)
        if not project:
            continue
        if snapshot.status not in {"active", "warming_up", "blocked"} and snapshot.manual_state != "pinned":
            continue
        active_project_map[str(project.id)] = project
        projects.append(project)
    if len(projects) < 8:
        existing_ids = {str(project.id) for project in projects}
        for project in active_project_map.values():
            if str(project.id) in existing_ids:
                continue
            projects.append(project)
            existing_ids.add(str(project.id))
            if len(projects) >= 8:
                break

    connections = await store.list_story_connections(session, limit=20)
    connection_summaries = [
        {
            "subject_ref": f"{item.source_ref} <-> {item.target_ref}",
            "mentions": item.evidence_count,
        }
        for item in connections[:10]
    ]
    digest_zone = ZoneInfo(settings.digest_timezone)
    reminders_due_today = [
        reminder
        for reminder in reminders
        if reminder.next_fire_at and reminder.next_fire_at.astimezone(digest_zone).date() == digest_date
    ]

    fallback_tasks = _fallback_task_recommendations(tasks, open_loops, projects)
    fallback_projects = []
    for project in projects[:5]:
        snapshot = snapshot_map.get(str(project.id))
        fallback_projects.append(
            {
                "project": project.title,
                "where_it_stands": _shorten(getattr(snapshot, "implemented", None) or getattr(project, "content", None) or "Active but still sparse."),
                "implemented": _shorten(getattr(snapshot, "implemented", None) or "Need stronger implementation evidence."),
                "left": _shorten(getattr(snapshot, "remaining", None) or "Need clearer remaining-work signal."),
                "holes": _shorten(", ".join(getattr(snapshot, "holes", []) or []) or "Need clearer critique coverage."),
                "next_step": _shorten(getattr(snapshot, "what_changed", None) or f"Push {project.title} with one concrete move."),
            }
        )
    fallback_topics = _fallback_writing_topics(projects, open_loops)
    fallback_videos = _fallback_video_recommendations(projects, connection_summaries)
    fallback_teasers = _fallback_brain_teasers(digest_date)
    fallback_improvements = [
        {
            "title": f"Sharpen {project.title}",
            "why": _shorten(
                (getattr(snapshot_map.get(str(project.id)), "holes", []) or ["This project still has unclear weak spots."])[0]
            ),
        }
        for project in projects[:2]
    ] or [{"title": "Clarify current focus", "why": "The brain still needs clearer active-project prioritization."}]
    voice_profile = (
        {
            "summary": voice_profile_record.summary,
            "traits": voice_profile_record.traits,
            "style_anchors": voice_profile_record.style_anchors[:3],
        }
        if voice_profile_record
        else None
    )

    headline = f"{digest_date.isoformat()} operating brief"
    narrative = "The brain has fresh signals, but the higher-order brief is still converging."
    recommended_tasks = fallback_tasks
    project_assessments = fallback_projects
    writing_topic_items = fallback_topics
    video_recommendations = fallback_videos
    brain_teasers = fallback_teasers
    improvement_focus = fallback_improvements
    low_confidence_sections: list[str] = []

    context_text = _build_digest_context(
        digest_date=digest_date,
        trigger=trigger,
        tasks=tasks,
        projects=projects,
        project_snapshots=snapshot_map,
        resources=resources,
        recent_activity=recent_activity,
        pending_reviews=pending_reviews,
        open_loops=open_loops,
        story_connections=connection_summaries,
        project_updates=project_updates,
        reminders_due_today=reminders_due_today,
        synapses=synapses,
        brain_learnings=brain_learnings,
        blind_spots=blind_spots,
        voice_profile=voice_profile,
    )

    try:
        composed = await compose_digest_sections(
            session,
            digest_date=digest_date.isoformat(),
            trigger=trigger,
            context_text=context_text,
        )
        headline = _shorten(str(composed.get("headline") or headline), 180)
        narrative = _shorten(str(composed.get("narrative") or narrative), 1800)
        improvement_focus, _ = _normalize_topic_items(
            composed.get("improvement_focus"),
            fallback_improvements,
        )
        recommended_tasks, _ = _normalize_topic_items(
            composed.get("recommended_tasks"),
            fallback_tasks,
        )
        project_assessments, _ = _normalize_topic_items(
            composed.get("project_assessments"),
            fallback_projects,
            title_key="project",
        )
        writing_topic_items, writing_topics = _normalize_topic_items(
            composed.get("writing_topics"),
            fallback_topics,
        )
        video_recommendations, _ = _normalize_topic_items(
            composed.get("video_recommendations"),
            fallback_videos,
        )
        brain_teasers, _ = _normalize_topic_items(
            composed.get("brain_teasers"),
            fallback_teasers,
        )
        low_confidence_sections = [
            str(item).strip()
            for item in composed.get("low_confidence_sections") or []
            if str(item).strip()
        ]
    except Exception as exc:  # pragma: no cover - fallback path
        log.warning("Falling back to deterministic digest sections: %s", exc)
        writing_topics = [item["title"] for item in writing_topic_items]
        low_confidence_sections = ["narrative", "youtube_recommendations"]
    else:
        writing_topics = [item["title"] for item in writing_topic_items]

    live_video_recommendations = await search_youtube_learning_queries(
        topics=[
            *(project.title for project in projects[:5]),
            *(item.get("title") for item in improvement_focus[:2] if item.get("title")),
            *(item.get("project") for item in project_assessments[:3] if item.get("project")),
        ]
    )
    if live_video_recommendations:
        video_recommendations = live_video_recommendations
    else:
        low_confidence_sections = sorted(set([*low_confidence_sections, "youtube_recommendations"]))

    return {
        "digest_date": digest_date.isoformat(),
        "headline": headline,
        "narrative": narrative,
        "tasks": [
            {
                "id": str(task.id),
                "title": task.title,
                "priority": getattr(task, "priority", "medium"),
                "status": getattr(task, "status", "active"),
            }
            for task in tasks
        ],
        "recommended_tasks": recommended_tasks[:10],
        "projects": [
            {
                "id": str(project.id),
                "title": project.title,
                "status": getattr(snapshot_map.get(str(project.id)), "status", None) or getattr(project, "status", "active"),
                "active_score": getattr(snapshot_map.get(str(project.id)), "active_score", 0),
                "implemented": getattr(snapshot_map.get(str(project.id)), "implemented", None),
                "remaining": getattr(snapshot_map.get(str(project.id)), "remaining", None),
                "holes": getattr(snapshot_map.get(str(project.id)), "holes", []) or [],
                "updates": project_updates.get(str(project.id), [])[:5],
            }
            for project in projects
        ],
        "project_assessments": project_assessments[:5],
        "recent_activity": [
            {
                "id": str(entry.id),
                "title": entry.title,
                "entry_type": entry.entry_type,
                "actor_name": entry.actor_name,
                "decision": getattr(entry, "decision", None),
                "impact": getattr(entry, "impact", None),
                "open_question": getattr(entry, "open_question", None),
                "happened_at": str(entry.happened_at),
            }
            for entry in recent_activity[:10]
        ],
        "pending_reviews": [
            {
                "id": str(review.id),
                "question": review.question,
            }
            for review in pending_reviews[:10]
        ],
        "open_loops": open_loops[:10],
        "synapses": synapses[:5],
        "brain_learnings": brain_learnings[:5],
        "blind_spots": blind_spots[:5],
        "story_connections": connection_summaries[:10],
        "writing_topics": writing_topics[:5],
        "writing_topic_items": writing_topic_items[:5],
        "video_recommendations": video_recommendations[:5],
        "brain_teasers": brain_teasers[:5],
        "reminders_due_today": [
            {
                "id": str(reminder.id),
                "title": reminder.title,
                "next_fire_at": str(reminder.next_fire_at) if reminder.next_fire_at else None,
            }
            for reminder in reminders_due_today[:10]
        ],
        "improvement_focus": improvement_focus[:5],
        "voice_profile": voice_profile,
        "low_confidence_sections": low_confidence_sections,
        "digest_preferences": digest_preference.sections if digest_preference else _default_digest_sections(),
        "narration_script": narrative,
    }


async def generate_or_refresh_digest(session, *, digest_date: date, trigger: str = "scheduled") -> dict:
    if store is None:
        raise RuntimeError("store module is unavailable")

    payload = await build_daily_digest_payload(session, digest_date=digest_date, trigger=trigger)
    existing = await store.get_digest_by_date(session, digest_date)
    if existing:
        existing.payload = payload
        await session.commit()
        await session.refresh(existing)
        return payload

    await store.create_digest(session, digest_date=digest_date, payload=payload)
    return payload
