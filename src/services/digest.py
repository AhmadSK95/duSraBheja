"""Daily digest assembly from stored notes, story events, and grounded synthesis."""

from __future__ import annotations

import logging
from datetime import date, timedelta

from src.agents.storyteller import compose_digest_sections

try:
    from src.lib import store
except ModuleNotFoundError:  # pragma: no cover - allows unit tests without app deps
    store = None

log = logging.getLogger("brain.digest")


def _shorten(value: str | None, limit: int = 220) -> str:
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1].rstrip()}…"


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


def _build_digest_context(
    *,
    digest_date: date,
    trigger: str,
    tasks: list,
    projects: list,
    resources: list,
    recent_activity: list,
    pending_reviews: list,
    open_loops: list[dict],
    story_connections: list[dict],
    project_updates: dict[str, list[dict]],
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
    for project in projects[:8]:
        lines.append(
            "- "
            f"{project.title} | status={getattr(project, 'status', 'active')} | "
            f"summary={_shorten(getattr(project, 'content', None), 240) or 'none'}"
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
            for resource in resources[:6]
        )
    if recent_activity:
        lines.extend(["", "Recent Activity:"])
        for entry in recent_activity[:10]:
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
    if open_loops:
        lines.extend(["", "Open Loops:"])
        lines.extend(f"- {item.get('open_question') or item.get('title')}" for item in open_loops[:8])
    if story_connections:
        lines.extend(["", "Cross-Project Connections:"])
        lines.extend(f"- {item['subject_ref']} ({item['mentions']} mentions)" for item in story_connections[:8])
    lines.extend(
        [
            "",
            "Instructions:",
            "- Recommend strong tasks for today.",
            "- Assess active projects with what is implemented, what is left, and what looks weak.",
            "- Suggest five writing topics.",
            "- Suggest five YouTube watch ideas as search queries unless grounded links exist.",
            "- Generate five smart brain teasers.",
        ]
    )
    return "\n".join(lines)


async def build_daily_digest_payload(session, *, digest_date: date, trigger: str = "scheduled") -> dict:
    if store is None:
        raise RuntimeError("store module is unavailable")

    tasks = await store.list_notes(session, category="task", limit=10)
    resources = await store.list_notes(session, category="resource", limit=10)
    recent_activity = await store.list_recent_activity(session, limit=20)
    pending_reviews = await store.get_pending_reviews(session)

    recent_cutoff = digest_date - timedelta(days=7)
    project_updates: dict[str, list[dict]] = {}
    open_loops = []
    subject_counter: dict[str, int] = {}
    active_project_map: dict[str, object] = {}
    for entry in recent_activity:
        if entry.happened_at.date() < recent_cutoff:
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
            if project:
                active_project_map[str(project.id)] = project
        subject_ref = getattr(entry, "subject_ref", None)
        if subject_ref:
            subject_counter[subject_ref] = subject_counter.get(subject_ref, 0) + 1

    projects = list(active_project_map.values())
    if len(projects) < 10:
        fallback_projects = await store.list_notes(session, category="project", limit=15)
        existing_ids = {str(project.id) for project in projects}
        for project in fallback_projects:
            if str(project.id) in existing_ids:
                continue
            projects.append(project)
            existing_ids.add(str(project.id))
            if len(projects) >= 10:
                break

    connection_summaries = [
        {"subject_ref": subject_ref, "mentions": count}
        for subject_ref, count in sorted(subject_counter.items(), key=lambda item: item[1], reverse=True)
        if count > 1
    ]

    fallback_tasks = _fallback_task_recommendations(tasks, open_loops, projects)
    fallback_projects = _fallback_project_assessments(projects, project_updates)
    fallback_topics = _fallback_writing_topics(projects, open_loops)
    fallback_videos = _fallback_video_recommendations(projects, connection_summaries)
    fallback_teasers = _fallback_brain_teasers(digest_date)

    headline = f"{digest_date.isoformat()} operating brief"
    narrative = "The brain has fresh signals, but the higher-order brief is still sparse."
    recommended_tasks = fallback_tasks
    project_assessments = fallback_projects
    writing_topic_items = fallback_topics
    video_recommendations = fallback_videos
    brain_teasers = fallback_teasers

    context_text = _build_digest_context(
        digest_date=digest_date,
        trigger=trigger,
        tasks=tasks,
        projects=projects,
        resources=resources,
        recent_activity=recent_activity,
        pending_reviews=pending_reviews,
        open_loops=open_loops,
        story_connections=connection_summaries,
        project_updates=project_updates,
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
    except Exception as exc:  # pragma: no cover - fallback path
        log.warning("Falling back to deterministic digest sections: %s", exc)
        writing_topics = [item["title"] for item in writing_topic_items]
    else:
        writing_topics = [item["title"] for item in writing_topic_items]

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
                "status": getattr(project, "status", "active"),
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
        "story_connections": connection_summaries[:10],
        "writing_topics": writing_topics[:5],
        "writing_topic_items": writing_topic_items[:5],
        "video_recommendations": video_recommendations[:5],
        "brain_teasers": brain_teasers[:5],
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
