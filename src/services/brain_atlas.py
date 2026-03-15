"""Derived read models and visual atlas snapshots for the brain dashboard."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.lib import store
from src.lib.provenance import DERIVED_ENTRY_TYPES, signal_kind_for_artifact, signal_kind_for_event
from src.lib.time import (
    coerce_datetime,
    describe_event_time,
    display_timezone,
    ensure_utc,
    format_display_datetime,
    local_date_label,
)
from src.services.openai_web import research_topic_brief

FACET_TYPES = (
    "projects",
    "interests",
    "people",
    "ideas",
    "thoughts",
    "media",
    "stories",
    "systems",
)

FACET_COLORS = {
    "projects": "#f97316",
    "interests": "#f59e0b",
    "people": "#14b8a6",
    "ideas": "#38bdf8",
    "thoughts": "#8b5cf6",
    "media": "#ec4899",
    "stories": "#ef4444",
    "systems": "#64748b",
}

FACET_ICONS = {
    "projects": "P",
    "interests": "I",
    "people": "Pe",
    "ideas": "Id",
    "thoughts": "Th",
    "media": "M",
    "stories": "S",
    "systems": "Sy",
}

KEYWORD_STOPWORDS = {
    "ahmad",
    "google",
    "youtube",
    "search",
    "searches",
    "video",
    "videos",
    "project",
    "projects",
    "work",
    "working",
    "latest",
    "recent",
    "brain",
    "dashboard",
    "chrome",
}


@dataclass(slots=True)
class BrainFacet:
    id: str
    facet_type: str
    title: str
    summary: str
    attention_score: float
    recency_score: float
    signal_kind: str
    created_at_utc: str | None
    happened_at_utc: str | None
    created_at_local: str | None
    happened_at_local: str | None
    display_timezone: str
    open_loops: list[str] = field(default_factory=list)
    related_ids: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["color"] = FACET_COLORS.get(self.facet_type, "#94a3b8")
        payload["icon"] = FACET_ICONS.get(self.facet_type, "?")
        return payload


@dataclass(slots=True)
class FacetLink:
    source_id: str
    target_id: str
    relation: str
    weight: float
    evidence_count: int
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class StoryRiverEvent:
    id: str
    title: str
    summary: str
    event_type: str
    signal_kind: str
    happened_at_utc: str | None
    happened_at_local: str | None
    event_day_label: str | None
    related_facet_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SubconsciousInsight:
    id: str
    lane: str
    certainty: str
    title: str
    summary: str
    why_now: str
    related_facet_ids: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FacetSnapshot:
    generated_at_local: str
    display_timezone: str
    facets: list[BrainFacet]
    links: list[FacetLink]
    story_river: list[StoryRiverEvent]
    subconscious: list[SubconsciousInsight]
    health: dict[str, Any]
    library_preview: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "generated_at_local": self.generated_at_local,
            "display_timezone": self.display_timezone,
            "facets": [facet.as_dict() for facet in self.facets],
            "links": [link.as_dict() for link in self.links],
            "story_river": [event.as_dict() for event in self.story_river],
            "subconscious": [insight.as_dict() for insight in self.subconscious],
            "health": self.health,
            "library_preview": list(self.library_preview),
        }


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_key(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _truncate(value: str | None, *, limit: int = 220) -> str:
    cleaned = " ".join((value or "").split()).strip()
    if not cleaned:
        return ""
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _recency_score(value: datetime | str | None, *, now: datetime | None = None) -> float:
    parsed = coerce_datetime(value)
    if parsed is None:
        return 0.08
    current = now or _utcnow()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    age = current - parsed.astimezone(timezone.utc)
    if age <= timedelta(hours=12):
        return 1.0
    if age <= timedelta(days=1):
        return 0.9
    if age <= timedelta(days=3):
        return 0.7
    if age <= timedelta(days=7):
        return 0.5
    if age <= timedelta(days=30):
        return 0.25
    return 0.1


def _utc_iso(value: datetime | str | None) -> str | None:
    parsed = ensure_utc(value)
    return parsed.isoformat() if parsed else None


def _safe_list(values: list[str | None], *, limit: int = 5) -> list[str]:
    seen: set[str] = set()
    items: list[str] = []
    for value in values:
        cleaned = _truncate(value, limit=180)
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(cleaned)
        if len(items) >= limit:
            break
    return items


def _note_facet_type(note_category: str | None, *, capture_intent: str | None = None) -> str:
    category = (note_category or "").lower()
    intent = (capture_intent or "").lower()
    if category == "project":
        return "projects"
    if category == "people":
        return "people"
    if category == "idea" or intent == "idea":
        return "ideas"
    if intent in {"thought", "question", "critique"}:
        return "thoughts"
    return "interests" if category == "resource" else "thoughts"


def _make_evidence(
    *,
    title: str,
    summary: str | None,
    signal_kind: str,
    happened_at: datetime | str | None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "title": _truncate(title, limit=120),
        "summary": _truncate(summary, limit=220),
        "signal_kind": signal_kind,
        "happened_at_local": format_display_datetime(happened_at, fallback="unknown"),
        **describe_event_time(happened_at),
        "metadata": metadata or {},
    }


def _project_facet(project, snapshot) -> BrainFacet:
    summary = _truncate(
        snapshot.implemented or snapshot.what_changed or project.content or "Project state is still forming.",
        limit=240,
    )
    open_loops = _safe_list(
        [snapshot.remaining, *(snapshot.blockers or []), *(snapshot.holes or [])],
        limit=5,
    )
    evidence = [
        _make_evidence(
            title="Where it stands",
            summary=snapshot.implemented or project.content,
            signal_kind="derived_system",
            happened_at=snapshot.updated_at,
            metadata={"status": snapshot.status},
        ),
        _make_evidence(
            title="What changed",
            summary=snapshot.what_changed,
            signal_kind="derived_system",
            happened_at=snapshot.last_signal_at or snapshot.updated_at,
        ),
    ]
    return BrainFacet(
        id=f"facet:project:{project.id}",
        facet_type="projects",
        title=project.title,
        summary=summary,
        attention_score=round(float(snapshot.active_score or 0.0), 3),
        recency_score=round(_recency_score(snapshot.last_signal_at or snapshot.updated_at), 3),
        signal_kind="direct_agent" if snapshot.manual_state == "pinned" else "derived_system",
        created_at_utc=_utc_iso(project.created_at),
        happened_at_utc=_utc_iso(snapshot.last_signal_at or snapshot.updated_at),
        created_at_local=format_display_datetime(project.created_at),
        happened_at_local=format_display_datetime(snapshot.last_signal_at or snapshot.updated_at),
        display_timezone=display_timezone().key,
        open_loops=open_loops,
        evidence=evidence,
        metadata={
            "project_id": str(project.id),
            "status": snapshot.status,
            "manual_state": snapshot.manual_state,
            "remaining": snapshot.remaining,
            "what_changed": snapshot.what_changed,
            "why_active": snapshot.why_active,
            "why_not_active": snapshot.why_not_active,
        },
    )


def _note_facet(note, *, capture_intent: str | None = None, title_override: str | None = None) -> BrainFacet:
    facet_type = _note_facet_type(note.category, capture_intent=capture_intent)
    signal_kind = "direct_human"
    summary = _truncate(note.content or note.title, limit=220)
    return BrainFacet(
        id=f"facet:note:{note.id}",
        facet_type=facet_type,
        title=title_override or note.title,
        summary=summary,
        attention_score=round(max(0.2, _recency_score(note.updated_at)), 3),
        recency_score=round(_recency_score(note.updated_at), 3),
        signal_kind=signal_kind,
        created_at_utc=_utc_iso(note.created_at),
        happened_at_utc=_utc_iso(note.updated_at),
        created_at_local=format_display_datetime(note.created_at),
        happened_at_local=format_display_datetime(note.updated_at),
        display_timezone=display_timezone().key,
        open_loops=[],
        evidence=[
            _make_evidence(
                title=note.title,
                summary=note.content,
                signal_kind=signal_kind,
                happened_at=note.updated_at or note.created_at,
                metadata={"category": note.category},
            )
        ],
        metadata={"category": note.category, "priority": note.priority, "status": note.status},
    )


def _artifact_facet(item: dict) -> BrainFacet | None:
    artifact = item["artifact"]
    capture_intent = item.get("capture_intent")
    facet_type = _note_facet_type(item.get("category"), capture_intent=capture_intent)
    if facet_type not in {"ideas", "thoughts"}:
        return None
    signal_kind = signal_kind_for_artifact(
        source=getattr(artifact, "source", None),
        capture_context=(artifact.metadata_ or {}).get("capture_context"),
    )
    title = artifact.summary or artifact.content_type.title()
    return BrainFacet(
        id=f"facet:artifact:{artifact.id}",
        facet_type=facet_type,
        title=_truncate(title, limit=110),
        summary=_truncate(artifact.raw_text or artifact.summary, limit=220),
        attention_score=round(max(0.2, _recency_score(artifact.created_at)), 3),
        recency_score=round(_recency_score(artifact.created_at), 3),
        signal_kind=signal_kind,
        created_at_utc=_utc_iso(artifact.created_at),
        happened_at_utc=_utc_iso(artifact.created_at),
        created_at_local=format_display_datetime(artifact.created_at),
        happened_at_local=format_display_datetime(artifact.created_at),
        display_timezone=display_timezone().key,
        open_loops=[],
        evidence=[
            _make_evidence(
                title=title,
                summary=artifact.raw_text or artifact.summary,
                signal_kind=signal_kind,
                happened_at=artifact.created_at,
                metadata={
                    "category": item.get("category"),
                    "capture_intent": capture_intent,
                    "validation_status": item.get("validation_status"),
                },
            )
        ],
        metadata={
            "category": item.get("category"),
            "capture_intent": capture_intent,
            "validation_status": item.get("validation_status"),
        },
    )


def _story_facet_from_board(board) -> BrainFacet:
    payload = dict(board.payload or {})
    summary = _truncate(payload.get("story") or payload.get("headline") or board.board_type, limit=240)
    open_loops = _safe_list(payload.get("carry_forward") or payload.get("open_loops") or [], limit=5)
    return BrainFacet(
        id=f"facet:board:{board.id}",
        facet_type="stories",
        title=f"{str(board.board_type).title()} Board",
        summary=summary,
        attention_score=round(0.55 + _recency_score(board.updated_at) * 0.35, 3),
        recency_score=round(_recency_score(board.updated_at), 3),
        signal_kind="direct_sync",
        created_at_utc=_utc_iso(board.created_at),
        happened_at_utc=_utc_iso(board.coverage_end),
        created_at_local=format_display_datetime(board.created_at),
        happened_at_local=format_display_datetime(board.coverage_end),
        display_timezone=display_timezone().key,
        open_loops=open_loops,
        evidence=[
            _make_evidence(
                title=payload.get("coverage_label") or board.generated_for_date.isoformat(),
                summary=payload.get("story"),
                signal_kind="direct_sync",
                happened_at=board.coverage_end,
                metadata={"board_type": board.board_type},
            )
        ],
        metadata={
            "board_type": board.board_type,
            "coverage_label": payload.get("coverage_label"),
            "generated_for_date": board.generated_for_date.isoformat(),
        },
    )


def _story_facet_from_entry(entry) -> BrainFacet | None:
    if (entry.entry_type or "") not in {"progress_update", "session_closeout", "decision"}:
        return None
    signal_kind = signal_kind_for_event(entry_type=entry.entry_type, actor_type=entry.actor_type)
    return BrainFacet(
        id=f"facet:story-entry:{entry.id}",
        facet_type="stories",
        title=_truncate(entry.title, limit=110),
        summary=_truncate(entry.summary or entry.body_markdown or entry.title, limit=220),
        attention_score=round(0.45 + _recency_score(entry.happened_at) * 0.45, 3),
        recency_score=round(_recency_score(entry.happened_at), 3),
        signal_kind=signal_kind,
        created_at_utc=_utc_iso(entry.created_at),
        happened_at_utc=_utc_iso(entry.happened_at),
        created_at_local=format_display_datetime(entry.created_at),
        happened_at_local=format_display_datetime(entry.happened_at),
        display_timezone=display_timezone().key,
        open_loops=_safe_list([entry.open_question], limit=2),
        evidence=[
            _make_evidence(
                title=entry.title,
                summary=entry.summary or entry.body_markdown,
                signal_kind=signal_kind,
                happened_at=entry.happened_at,
                metadata={"entry_type": entry.entry_type, "actor_name": entry.actor_name},
            )
        ],
        metadata={"entry_type": entry.entry_type, "actor_name": entry.actor_name},
    )


def _system_facets(sync_runs: list, reviews: list, traces: list, eval_runs: list) -> list[BrainFacet]:
    by_source: dict[str, list] = defaultdict(list)
    for run in sync_runs:
        by_source[str(run.sync_source_id)].append(run)

    facets: list[BrainFacet] = []
    for source_id, runs in list(by_source.items())[:6]:
        latest = sorted(runs, key=lambda item: item.started_at, reverse=True)[0]
        facets.append(
            BrainFacet(
                id=f"facet:system:sync:{source_id}",
                facet_type="systems",
                title=f"Sync {source_id[:8]}",
                summary=_truncate(
                    f"Latest {latest.mode} run finished with status={latest.status}, seen={latest.items_seen}, imported={latest.items_imported}.",
                    limit=220,
                ),
                attention_score=round(0.35 + _recency_score(latest.started_at) * 0.35, 3),
                recency_score=round(_recency_score(latest.started_at), 3),
                signal_kind="direct_sync",
                created_at_utc=_utc_iso(latest.started_at),
                happened_at_utc=_utc_iso(latest.finished_at or latest.started_at),
                created_at_local=format_display_datetime(latest.started_at),
                happened_at_local=format_display_datetime(latest.finished_at or latest.started_at),
                display_timezone=display_timezone().key,
                evidence=[
                    _make_evidence(
                        title="Latest sync run",
                        summary=f"mode={latest.mode}, status={latest.status}, seen={latest.items_seen}, imported={latest.items_imported}",
                        signal_kind="direct_sync",
                        happened_at=latest.started_at,
                    )
                ],
                metadata={"status": latest.status, "mode": latest.mode},
            )
        )

    if reviews:
        facets.append(
            BrainFacet(
                id="facet:system:review-queue",
                facet_type="systems",
                title="Review Queue",
                summary=f"{len(reviews)} captures still need moderation before they influence boards or retrieval.",
                attention_score=0.74,
                recency_score=round(_recency_score(reviews[0].created_at), 3),
                signal_kind="derived_system",
                created_at_utc=_utc_iso(reviews[0].created_at),
                happened_at_utc=_utc_iso(reviews[0].created_at),
                created_at_local=format_display_datetime(reviews[0].created_at),
                happened_at_local=format_display_datetime(reviews[0].created_at),
                display_timezone=display_timezone().key,
                open_loops=_safe_list([review.question for review in reviews], limit=3),
                evidence=[
                    _make_evidence(
                        title=review.review_kind,
                        summary=review.question,
                        signal_kind="derived_system",
                        happened_at=review.created_at,
                    )
                    for review in reviews[:3]
                ],
                metadata={"pending_reviews": len(reviews)},
            )
        )

    if traces:
        failures = [trace for trace in traces if trace.failure_stage]
        latest = traces[0]
        facets.append(
            BrainFacet(
                id="facet:system:retrieval",
                facet_type="systems",
                title="Ask Brain Reliability",
                summary=_truncate(
                    f"Latest traces show {len(failures)} stage failures across the recent sample. Most recent query mode was {latest.resolved_mode}.",
                    limit=220,
                ),
                attention_score=0.68 if failures else 0.42,
                recency_score=round(_recency_score(latest.created_at), 3),
                signal_kind="derived_system",
                created_at_utc=_utc_iso(latest.created_at),
                happened_at_utc=_utc_iso(latest.created_at),
                created_at_local=format_display_datetime(latest.created_at),
                happened_at_local=format_display_datetime(latest.created_at),
                display_timezone=display_timezone().key,
                open_loops=[],
                evidence=[
                    _make_evidence(
                        title=trace.question[:100],
                        summary=f"mode={trace.resolved_mode}, failure_stage={trace.failure_stage or 'ok'}",
                        signal_kind="derived_system",
                        happened_at=trace.created_at,
                    )
                    for trace in traces[:3]
                ],
                metadata={"recent_failures": len(failures)},
            )
        )

    if eval_runs:
        latest = eval_runs[0]
        summary = latest.summary or {}
        facets.append(
            BrainFacet(
                id="facet:system:evals",
                facet_type="systems",
                title="Regression Harness",
                summary=_truncate(
                    f"Latest eval run `{latest.run_name}` finished with status={latest.status} and summary={summary}.",
                    limit=220,
                ),
                attention_score=0.45,
                recency_score=round(_recency_score(latest.created_at), 3),
                signal_kind="derived_system",
                created_at_utc=_utc_iso(latest.created_at),
                happened_at_utc=_utc_iso(latest.updated_at),
                created_at_local=format_display_datetime(latest.created_at),
                happened_at_local=format_display_datetime(latest.updated_at),
                display_timezone=display_timezone().key,
                evidence=[
                    _make_evidence(
                        title=latest.run_name,
                        summary=str(summary),
                        signal_kind="derived_system",
                        happened_at=latest.updated_at,
                    )
                ],
                metadata={"status": latest.status},
            )
        )

    return facets


def _interest_and_media_facets(chrome_rows: list[dict]) -> tuple[list[BrainFacet], list[BrainFacet]]:
    interest_counts: Counter[str] = Counter()
    interest_refs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    media_counts: Counter[str] = Counter()
    media_refs: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in chrome_rows:
        source_item = row["source_item"]
        payload = dict(source_item.payload or {})
        metadata = dict(payload.get("metadata") or {})
        title = source_item.title
        signal_time = source_item.happened_at or source_item.created_at
        for theme in metadata.get("keyword_themes") or []:
            term = str(theme.get("term") or "").strip()
            if not term or term.lower() in KEYWORD_STOPWORDS:
                continue
            interest_counts[term] += int(theme.get("count") or 1)
            interest_refs[term].append(
                _make_evidence(
                    title=title,
                    summary=source_item.summary,
                    signal_kind="direct_sync",
                    happened_at=signal_time,
                    metadata={"entry_type": payload.get("entry_type")},
                )
            )
        included_examples = metadata.get("included_examples") or {}
        for bucket_name in ("youtube_watch", "ott_visit", "youtube_search"):
            for example in included_examples.get(bucket_name) or []:
                label = str(example.get("label") or example.get("title") or "").strip()
                if not label:
                    continue
                media_counts[label] += int(example.get("count") or 1)
                media_refs[label].append(
                    _make_evidence(
                        title=label,
                        summary=example.get("why") or source_item.summary,
                        signal_kind="direct_sync",
                        happened_at=signal_time,
                        metadata={"bucket": bucket_name},
                    )
                )

    interest_facets = [
        BrainFacet(
            id=f"facet:interest:{_normalize_key(term)}",
            facet_type="interests",
            title=term,
            summary=f"Recurring interest signal across Chrome activity with {count} weighted mentions.",
            attention_score=min(0.95, 0.25 + count / 8),
            recency_score=0.55,
            signal_kind="direct_sync",
            created_at_utc=None,
            happened_at_utc=interest_refs[term][0]["event_time_utc"] if interest_refs[term] else None,
            created_at_local=None,
            happened_at_local=interest_refs[term][0]["happened_at_local"] if interest_refs[term] else None,
            display_timezone=display_timezone().key,
            evidence=interest_refs[term][:3],
            metadata={"count": count},
        )
        for term, count in interest_counts.most_common(8)
    ]

    media_facets = [
        BrainFacet(
            id=f"facet:media:{_normalize_key(label)}",
            facet_type="media",
            title=label,
            summary=f"Media pattern surfacing repeatedly in Chrome activity with {count} weighted mentions.",
            attention_score=min(0.9, 0.24 + count / 8),
            recency_score=0.52,
            signal_kind="direct_sync",
            created_at_utc=None,
            happened_at_utc=media_refs[label][0]["event_time_utc"] if media_refs[label] else None,
            created_at_local=None,
            happened_at_local=media_refs[label][0]["happened_at_local"] if media_refs[label] else None,
            display_timezone=display_timezone().key,
            evidence=media_refs[label][:3],
            metadata={"count": count},
        )
        for label, count in media_counts.most_common(8)
    ]
    return interest_facets, media_facets


def _story_river_events(boards: list, recent_activity: list) -> list[StoryRiverEvent]:
    events: list[StoryRiverEvent] = []
    for board in boards[:10]:
        payload = dict(board.payload or {})
        events.append(
            StoryRiverEvent(
                id=f"story-river:board:{board.id}",
                title=f"{str(board.board_type).title()} Board",
                summary=_truncate(payload.get("story") or payload.get("headline") or ""),
                event_type=f"{board.board_type}_board",
                signal_kind="direct_sync",
                happened_at_utc=_utc_iso(board.coverage_end),
                happened_at_local=format_display_datetime(board.coverage_end),
                event_day_label=local_date_label(board.coverage_end),
                metadata={
                    "coverage_label": payload.get("coverage_label"),
                    "board_id": str(board.id),
                },
            )
        )
    for entry in recent_activity[:20]:
        signal_kind = signal_kind_for_event(entry_type=entry.entry_type, actor_type=entry.actor_type)
        events.append(
            StoryRiverEvent(
                id=f"story-river:event:{entry.id}",
                title=entry.title,
                summary=_truncate(entry.summary or entry.body_markdown or entry.title),
                event_type=entry.entry_type,
                signal_kind=signal_kind,
                happened_at_utc=_utc_iso(entry.happened_at),
                happened_at_local=format_display_datetime(entry.happened_at),
                event_day_label=local_date_label(entry.happened_at),
                metadata={
                    "entry_type": entry.entry_type,
                    "actor_name": entry.actor_name,
                    "project_note_id": str(entry.project_note_id) if entry.project_note_id else None,
                },
            )
        )
    events.sort(key=lambda item: item.happened_at_local or "", reverse=True)
    return events[:24]


async def _subconscious_insights(
    session: AsyncSession,
    *,
    project_facets: list[BrainFacet],
    interest_facets: list[BrainFacet],
    media_facets: list[BrainFacet],
    story_events: list[StoryRiverEvent],
    recent_activity: list,
    story_connections: list,
    include_web: bool = False,
) -> list[SubconsciousInsight]:
    insights: list[SubconsciousInsight] = []

    open_questions = [
        entry
        for entry in recent_activity
        if getattr(entry, "open_question", None)
        and (getattr(entry, "entry_type", "") or "") not in DERIVED_ENTRY_TYPES
    ]
    for entry in open_questions[:3]:
        related_project_id = f"facet:project:{entry.project_note_id}" if entry.project_note_id else None
        insights.append(
            SubconsciousInsight(
                id=f"subconscious:replay:{entry.id}",
                lane="Replay",
                certainty="grounded observation",
                title=entry.title,
                summary=_truncate(entry.open_question or entry.summary),
                why_now="This is still unresolved in direct recent activity and deserves replay before it quietly decays.",
                related_facet_ids=[related_project_id] if related_project_id else [],
                evidence=[
                    _make_evidence(
                        title=entry.title,
                        summary=entry.open_question or entry.summary,
                        signal_kind=signal_kind_for_event(entry_type=entry.entry_type, actor_type=entry.actor_type),
                        happened_at=entry.happened_at,
                    )
                ],
            )
        )

    for connection in story_connections[:3]:
        insights.append(
            SubconsciousInsight(
                id=f"subconscious:map:{connection.id}",
                lane="Map",
                certainty="grounded observation",
                title=f"{connection.source_ref} ↔ {connection.target_ref}",
                summary=_truncate(
                    f"Relation={connection.relation}, weight={connection.weight:.2f}, evidence_count={connection.evidence_count}.",
                    limit=220,
                ),
                why_now="This relationship is already visible in the stored graph and should influence the atlas links.",
                related_facet_ids=[],
                evidence=[],
            )
        )

    if project_facets and (interest_facets or media_facets):
        companion = (interest_facets or media_facets)[0]
        project = project_facets[0]
        insights.append(
            SubconsciousInsight(
                id="subconscious:dream:cross-pollination",
                lane="Dream",
                certainty="speculative hypothesis",
                title=f"{companion.title} could feed {project.title}",
                summary=_truncate(
                    f"A quieter connection is forming between `{companion.title}` and the active project `{project.title}`. "
                    "This is a prompt to explore a non-obvious bridge rather than a claim that it already exists.",
                    limit=240,
                ),
                why_now="Associative recombination is valuable when your stored signals are strong but not yet connected explicitly.",
                related_facet_ids=[project.id, companion.id],
                evidence=[*project.evidence[:1], *companion.evidence[:1]],
            )
        )

    if project_facets:
        top_project = project_facets[0]
        foresight_summary = (
            "The current evidence suggests the best next step is to convert the latest open loop into a sharper experiment."
        )
        evidence = top_project.evidence[:2]
        certainty = "plausible inference"
        if include_web:
            try:
                web_brief = await research_topic_brief(
                    topic=top_project.title,
                    questions=top_project.open_loops[:2] or [top_project.summary],
                )
            except Exception:
                web_brief = None
            findings = list((web_brief or {}).get("findings") or [])
            if findings:
                foresight_summary = _truncate(
                    f"{foresight_summary} Web-grounded angle: {findings[0].get('summary') or findings[0].get('title')}.",
                    limit=240,
                )
                evidence.append(
                    {
                        "title": findings[0].get("title") or "Web finding",
                        "summary": findings[0].get("summary") or findings[0].get("source_hint"),
                        "signal_kind": "direct_sync",
                        "happened_at_local": None,
                        "event_time_utc": None,
                        "event_time_local": None,
                        "display_timezone": display_timezone().key,
                        "timezone_label": display_timezone().key,
                        "metadata": {"url": findings[0].get("url")},
                    }
                )
                certainty = "plausible inference"
        insights.append(
            SubconsciousInsight(
                id="subconscious:foresight:next-step",
                lane="Foresight",
                certainty=certainty,
                title=f"Next horizon for {top_project.title}",
                summary=foresight_summary,
                why_now="This is the quiet future-simulation layer: grounded direction, not noisy feed spam.",
                related_facet_ids=[top_project.id],
                evidence=evidence,
            )
        )

    return insights[:10]


def _build_links(facets: list[BrainFacet], story_connections: list) -> list[FacetLink]:
    links: list[FacetLink] = []
    by_key = {_normalize_key(facet.title): facet.id for facet in facets}
    seen: set[tuple[str, str, str]] = set()
    for connection in story_connections:
        source_id = by_key.get(_normalize_key(connection.source_ref))
        target_id = by_key.get(_normalize_key(connection.target_ref))
        if not source_id or not target_id or source_id == target_id:
            continue
        key = tuple(sorted((source_id, target_id)) + [connection.relation])
        if key in seen:
            continue
        seen.add(key)
        links.append(
            FacetLink(
                source_id=source_id,
                target_id=target_id,
                relation=connection.relation,
                weight=round(float(connection.weight or 0.0), 3),
                evidence_count=int(connection.evidence_count or 0),
                reason=f"Stored story connection: {connection.source_ref} {connection.relation} {connection.target_ref}",
            )
        )

    project_facets = [facet for facet in facets if facet.facet_type == "projects"]
    story_facets = [facet for facet in facets if facet.facet_type == "stories"]
    for project in project_facets[:8]:
        normalized_title = _normalize_key(project.title)
        for story in story_facets[:12]:
            combined = " ".join(
                [
                    story.title,
                    story.summary,
                    " ".join(item.get("summary") or "" for item in story.evidence[:2]),
                ]
            )
            if normalized_title and normalized_title in _normalize_key(combined):
                key = tuple(sorted((project.id, story.id)) + ["contextual_overlap"])
                if key in seen:
                    continue
                seen.add(key)
                links.append(
                    FacetLink(
                        source_id=project.id,
                        target_id=story.id,
                        relation="contextual_overlap",
                        weight=0.54,
                        evidence_count=1,
                        reason="Project title appears inside the story evidence.",
                    )
                )
    return links[:80]


def _library_item(
    *,
    item_id: str,
    title: str,
    summary: str,
    item_type: str,
    facet_type: str,
    source_name: str,
    category: str | None,
    capture_intent: str | None,
    happened_at: datetime | str | None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": item_id,
        "title": _truncate(title, limit=120),
        "summary": _truncate(summary, limit=220),
        "item_type": item_type,
        "facet_type": facet_type,
        "source_name": source_name,
        "category": category,
        "capture_intent": capture_intent,
        "happened_at_local": format_display_datetime(happened_at),
        **describe_event_time(happened_at),
        "metadata": metadata or {},
    }


async def build_library_items(
    session: AsyncSession,
    *,
    q: str | None = None,
    facet: str | None = None,
    source_name: str | None = None,
    category: str | None = None,
    capture_intent: str | None = None,
    limit: int = 240,
) -> list[dict[str, Any]]:
    artifacts = await store.list_artifact_interpretations(session, limit=140)
    notes = await store.list_notes(session, limit=120)
    source_rows = await store.list_source_items_with_sources(session, limit=120)
    recent_activity = await store.list_recent_activity(session, limit=120)

    items: list[dict[str, Any]] = []
    for item in artifacts:
        artifact = item["artifact"]
        items.append(
            _library_item(
                item_id=f"artifact:{artifact.id}",
                title=artifact.summary or artifact.content_type,
                summary=artifact.raw_text or artifact.summary or artifact.content_type,
                item_type="artifact",
                facet_type=_note_facet_type(item.get("category"), capture_intent=item.get("capture_intent")),
                source_name=getattr(artifact, "source", "artifact"),
                category=item.get("category"),
                capture_intent=item.get("capture_intent"),
                happened_at=artifact.created_at,
                metadata={"validation_status": item.get("validation_status")},
            )
        )
    for note in notes:
        items.append(
            _library_item(
                item_id=f"note:{note.id}",
                title=note.title,
                summary=note.content or note.title,
                item_type="note",
                facet_type=_note_facet_type(note.category),
                source_name="note",
                category=note.category,
                capture_intent=None,
                happened_at=note.updated_at or note.created_at,
            )
        )
    for row in source_rows:
        source_item = row["source_item"]
        sync_source = row["sync_source"]
        payload = dict(source_item.payload or {})
        entry_type = payload.get("entry_type")
        items.append(
            _library_item(
                item_id=f"source:{source_item.id}",
                title=source_item.title,
                summary=source_item.summary or source_item.title,
                item_type="source_item",
                facet_type="media" if sync_source.source_type == "chrome_activity" else "systems",
                source_name=sync_source.source_type,
                category=str(entry_type or "source_item"),
                capture_intent=None,
                happened_at=source_item.happened_at or source_item.created_at,
                metadata={"entry_type": entry_type},
            )
        )
    for entry in recent_activity:
        items.append(
            _library_item(
                item_id=f"journal:{entry.id}",
                title=entry.title,
                summary=entry.summary or entry.body_markdown or entry.title,
                item_type="journal_entry",
                facet_type="stories" if entry.entry_type in {"progress_update", "session_closeout", "decision"} else "thoughts",
                source_name=entry.actor_type,
                category=entry.entry_type,
                capture_intent=None,
                happened_at=entry.happened_at,
                metadata={"entry_type": entry.entry_type, "actor_name": entry.actor_name},
            )
        )

    lowered_q = (q or "").strip().lower()
    filtered: list[dict[str, Any]] = []
    for item in items:
        if facet and item["facet_type"] != facet:
            continue
        if source_name and item["source_name"] != source_name:
            continue
        if category and str(item.get("category") or "") != category:
            continue
        if capture_intent and str(item.get("capture_intent") or "") != capture_intent:
            continue
        haystack = " ".join(
            [
                str(item.get("title") or ""),
                str(item.get("summary") or ""),
                str(item.get("category") or ""),
                str(item.get("source_name") or ""),
                str(item.get("facet_type") or ""),
            ]
        ).lower()
        if lowered_q and lowered_q not in haystack:
            continue
        filtered.append(item)

    filtered.sort(key=lambda item: item.get("event_time_local") or "", reverse=True)
    return filtered[:limit]


async def build_brain_atlas_snapshot(
    session: AsyncSession,
    *,
    include_web: bool = False,
) -> FacetSnapshot:
    project_snapshots = await store.list_project_state_snapshots(session, limit=20)
    notes = await store.list_notes(session, limit=140)
    artifacts = await store.list_artifact_interpretations(session, limit=140)
    recent_activity = await store.list_recent_activity(session, limit=140)
    boards = await store.list_boards(session, limit=18)
    chrome_rows = await store.list_source_items_with_sources(session, source_type="chrome_activity", limit=80)
    story_connections = await store.list_story_connections(session, limit=60)
    sync_runs = await store.list_recent_sync_runs(session, limit=18)
    reviews = await store.get_pending_reviews(session)
    traces = await store.list_retrieval_traces(session, limit=18)
    eval_runs = await store.list_eval_runs(session, limit=8)
    library_preview = await build_library_items(session, limit=16)

    project_facets: list[BrainFacet] = []
    for snapshot in project_snapshots:
        project = await store.get_note(session, snapshot.project_note_id)
        if not project:
            continue
        project_facets.append(_project_facet(project, snapshot))

    note_facets: list[BrainFacet] = []
    for note in notes:
        if note.category == "project":
            continue
        if note.category in {"people", "idea", "note", "resource"}:
            note_facets.append(_note_facet(note))

    artifact_facets = [facet for facet in (_artifact_facet(item) for item in artifacts[:60]) if facet]
    interest_facets, media_facets = _interest_and_media_facets(chrome_rows)
    story_facets = [_story_facet_from_board(board) for board in boards[:6]]
    story_facets.extend(
        facet
        for facet in (_story_facet_from_entry(entry) for entry in recent_activity[:24])
        if facet
    )
    system_facets = _system_facets(sync_runs, reviews, traces, eval_runs)

    facets = [
        *project_facets[:12],
        *interest_facets[:8],
        *[facet for facet in note_facets if facet.facet_type == "people"][:8],
        *[facet for facet in note_facets if facet.facet_type == "ideas"][:10],
        *[facet for facet in note_facets if facet.facet_type == "thoughts"][:10],
        *media_facets[:8],
        *story_facets[:10],
        *system_facets[:8],
        *artifact_facets[:10],
    ]
    facets.sort(key=lambda facet: (facet.attention_score, facet.recency_score), reverse=True)
    facets = facets[:54]

    links = _build_links(facets, story_connections)
    story_river = _story_river_events(boards, recent_activity)
    subconscious = await _subconscious_insights(
        session,
        project_facets=project_facets,
        interest_facets=interest_facets,
        media_facets=media_facets,
        story_events=story_river,
        recent_activity=recent_activity,
        story_connections=story_connections,
        include_web=include_web,
    )

    health = {
        "display_timezone": display_timezone().key,
        "generated_at_local": format_display_datetime(_utcnow()),
        "project_count": len(project_facets),
        "facet_count": len(facets),
        "pending_review_count": len(reviews),
        "recent_trace_failures": len([trace for trace in traces if trace.failure_stage]),
        "latest_syncs": [
            {
                "source_id": str(run.sync_source_id),
                "mode": run.mode,
                "status": run.status,
                "started_at_local": format_display_datetime(run.started_at),
                "items_seen": run.items_seen,
                "items_imported": run.items_imported,
            }
            for run in sync_runs[:6]
        ],
    }

    return FacetSnapshot(
        generated_at_local=format_display_datetime(_utcnow()),
        display_timezone=display_timezone().key,
        facets=facets,
        links=links,
        story_river=story_river,
        subconscious=subconscious,
        health=health,
        library_preview=library_preview,
    )
