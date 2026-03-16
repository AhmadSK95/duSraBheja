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
ATLAS_EXCLUDED_NOTE_PREFIXES = ("Knowledge Base:",)
ATLAS_EXCLUDED_ARTIFACT_PREFIXES = ("Evidence gap:", "Research next step", "Knowledge Base:")
HEADSPACE_LOW_SIGNAL_HINTS = (
    "todo",
    "to-do",
    "checklist",
    "agent todo",
    "agent plan",
    "plan snapshot",
    "workspace summary",
    "workspace landscape",
    "repo signal",
    "directory inventory",
    "collector",
    "sync receipt",
    "chrome project signal",
    "chrome period summary",
    "chrome profile signal",
    "source item",
)

CURRENT_HEADSPACE_WINDOW_DAYS = 45
CURRENT_ARTIFACT_WINDOW_DAYS = 30
CURRENT_STORY_WINDOW_DAYS = 30
LEGACY_WORKSPACE_HINTS = ("/desktop/", "\\desktop\\")
CURRENT_WORKSPACE_HINTS = ("/code/", "/opt/dusrabheja", "/users/moenuddeenahmadshaik/code/")
CURATED_STORY_ENTRY_TYPES = {"progress_update", "session_closeout", "decision", "conversation_session"}
CURRENT_HEADSPACE_FACET_TYPES = {"projects", "ideas", "thoughts", "interests", "media", "stories"}
TEMPORAL_EVENT_WEIGHTS = {
    "progress_update": 1.0,
    "session_closeout": 0.96,
    "decision": 0.9,
    "daily_board": 0.84,
    "weekly_board": 0.8,
    "conversation_session": 0.72,
}
TEMPORAL_SIGNAL_WEIGHTS = {
    "direct_human": 1.0,
    "direct_agent": 0.94,
    "direct_sync": 0.7,
    "derived_system": 0.28,
}
TEMPORAL_DECAY = 0.58
TEMPORAL_NEIGHBOR_LIMIT = 4
NOISY_STORY_ENTRY_TYPES = {
    "chrome_project_signal",
    "chrome_period_summary",
    "chrome_profile_signal",
    "chrome_daily_signals",
    "knowledge_refresh",
    "voice_refresh",
    "blind_spot",
    "synapse",
    "research_thread",
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
class CurrentHeadspaceNode:
    facet_id: str
    title: str
    facet_type: str
    summary: str
    signal_kind: str
    happened_at_local: str | None
    path_score: float
    anchor_count: int
    why_now: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MemoryPath:
    id: str
    title: str
    summary: str
    anchor_title: str
    anchor_signal_kind: str
    anchor_time_local: str | None
    path_score: float
    related_facet_ids: list[str] = field(default_factory=list)
    provenance: str = ""

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
    current_headspace: list[CurrentHeadspaceNode]
    memory_paths: list[MemoryPath]
    health: dict[str, Any]
    library_preview: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "as_of_local": self.generated_at_local,
            "generated_at_local": self.generated_at_local,
            "display_timezone": self.display_timezone,
            "facet_count": len(self.facets),
            "link_count": len(self.links),
            "facets": [facet.as_dict() for facet in self.facets],
            "links": [link.as_dict() for link in self.links],
            "story_river": [event.as_dict() for event in self.story_river],
            "subconscious": [insight.as_dict() for insight in self.subconscious],
            "current_headspace": [node.as_dict() for node in self.current_headspace],
            "memory_paths": [path.as_dict() for path in self.memory_paths],
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


def _is_recent(value: datetime | str | None, *, now: datetime, days: int) -> bool:
    parsed = coerce_datetime(value)
    if parsed is None:
        return False
    return parsed.astimezone(timezone.utc) >= now - timedelta(days=days)


def _contains_legacy_workspace(value: str | None) -> bool:
    lowered = (value or "").lower()
    return any(hint in lowered for hint in LEGACY_WORKSPACE_HINTS)


def _contains_current_workspace(value: str | None) -> bool:
    lowered = (value or "").lower()
    return any(hint in lowered for hint in CURRENT_WORKSPACE_HINTS)


def _preferred_workspace_path(repos: list[Any]) -> str | None:
    preferred_paths = [str(getattr(repo, "local_path", "") or "") for repo in repos if getattr(repo, "local_path", None)]
    if not preferred_paths:
        return None
    non_legacy = [path for path in preferred_paths if not _contains_legacy_workspace(path)]
    if non_legacy:
        return non_legacy[0]
    return preferred_paths[0]


def _current_headspace_bonus(*, summary: str | None, title: str | None, happened_at: datetime | str | None, signal_kind: str) -> float:
    bonus = 0.0
    if signal_kind in {"direct_human", "direct_agent"}:
        bonus += 0.1
    elif signal_kind == "direct_sync":
        bonus += 0.04
    else:
        bonus -= 0.08
    if _contains_current_workspace(summary) or _contains_current_workspace(title):
        bonus += 0.08
    if _contains_legacy_workspace(summary) or _contains_legacy_workspace(title):
        bonus -= 0.14
    bonus += _recency_score(happened_at) * 0.08
    return bonus


def _low_signal_text_penalty(value: str | None) -> float:
    lowered = (value or "").strip().lower()
    if not lowered:
        return 0.0
    penalty = 0.0
    for hint in HEADSPACE_LOW_SIGNAL_HINTS:
        if hint in lowered:
            penalty += 0.1
    if "knowledge base:" in lowered:
        penalty += 0.16
    if "evidence gap:" in lowered or "research next step" in lowered:
        penalty += 0.18
    return min(0.42, penalty)


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


def _temporal_signal_weight(signal_kind: str | None) -> float:
    return TEMPORAL_SIGNAL_WEIGHTS.get(str(signal_kind or ""), 0.42)


def _story_event_temporal_weight(event_type: str | None) -> float:
    return TEMPORAL_EVENT_WEIGHTS.get(str(event_type or ""), 0.54)


def _facet_temporal_seed(facet: BrainFacet, *, now: datetime) -> float:
    base = _temporal_signal_weight(facet.signal_kind) * max(0.14, _recency_score(facet.happened_at_utc, now=now)) * 0.62
    evidence_items = list(facet.evidence or [])
    for item in evidence_items[:3]:
        base += (
            _temporal_signal_weight(str(item.get("signal_kind") or ""))
            * max(0.12, _recency_score(item.get("event_time_utc"), now=now))
            * 0.14
        )
    if facet.facet_type == "systems":
        base *= 0.45
    elif facet.facet_type == "stories":
        base *= 0.9
    elif facet.facet_type in {"thoughts", "ideas"} and facet.signal_kind == "direct_sync":
        base *= 0.5
    elif facet.facet_type in {"interests", "media"} and facet.signal_kind == "direct_sync":
        base *= 0.88
    if _contains_current_workspace(str(facet.metadata.get("workspace_path") or "")):
        base += 0.08
    base -= _low_signal_text_penalty(facet.title) * 0.55
    base -= _low_signal_text_penalty(facet.summary) * 0.7
    return round(base, 3)


def _is_low_signal_headspace_facet(facet: BrainFacet) -> bool:
    title_penalty = _low_signal_text_penalty(facet.title)
    summary_penalty = _low_signal_text_penalty(facet.summary)
    if title_penalty + summary_penalty >= 0.3:
        return True
    if facet.facet_type in {"thoughts", "ideas"} and facet.signal_kind == "direct_sync":
        return True
    return False


def _headspace_min_score(facet: BrainFacet, *, anchor_count: int) -> float:
    if facet.facet_type == "projects":
        return 0.44 if anchor_count else 0.5
    if facet.facet_type == "stories":
        return 0.46 if anchor_count else 0.54
    if facet.facet_type in {"ideas", "thoughts"}:
        return 0.5 if facet.signal_kind in {"direct_human", "direct_agent"} else 0.7
    if facet.facet_type in {"interests", "media"}:
        return 0.52 if anchor_count else 0.6
    return 0.58


def _infer_story_related_facet_ids(
    event: StoryRiverEvent,
    *,
    facets: list[BrainFacet],
) -> list[str]:
    facet_by_project_id = {
        str(facet.metadata.get("project_id")): facet.id
        for facet in facets
        if facet.facet_type == "projects" and facet.metadata.get("project_id")
    }
    title_index = {_normalize_key(facet.title): facet.id for facet in facets}
    combined_text = " ".join(
        filter(
            None,
            [
                event.title,
                event.summary,
                str(event.metadata.get("coverage_label") or ""),
            ],
        )
    )
    normalized_text = _normalize_key(combined_text)
    related: list[str] = []

    project_note_id = str(event.metadata.get("project_note_id") or "").strip()
    if project_note_id and project_note_id in facet_by_project_id:
        related.append(facet_by_project_id[project_note_id])

    for ref in list(event.metadata.get("related_refs") or []):
        facet_id = title_index.get(_normalize_key(str(ref)))
        if facet_id and facet_id not in related:
            related.append(facet_id)

    for facet in facets:
        if facet.facet_type not in CURRENT_HEADSPACE_FACET_TYPES:
            continue
        key = _normalize_key(facet.title)
        if key and key in normalized_text and facet.id not in related:
            related.append(facet.id)
        if len(related) >= 5:
            break
    return related[:5]


def _headspace_reason(*, facet: BrainFacet, path_score: float, anchor_count: int) -> str:
    if anchor_count >= 3:
        return "Multiple recent memory paths keep landing here."
    if anchor_count >= 1:
        return "A recent story anchor is still flowing into this node."
    if facet.signal_kind == "direct_human":
        return "Recent direct human signal keeps this close to the surface."
    if facet.signal_kind == "direct_agent":
        return "Recent agent work is keeping this mentally active."
    if path_score >= 0.8:
        return "It is still connected to several current signals, even if indirectly."
    return "It remains part of the active mental map right now."


def _build_temporal_traversal(
    facets: list[BrainFacet],
    story_river: list[StoryRiverEvent],
    links: list[FacetLink],
    *,
    now: datetime,
) -> tuple[list[CurrentHeadspaceNode], list[MemoryPath], dict[str, float]]:
    score_by_facet: dict[str, float] = defaultdict(float)
    anchor_counts: Counter[str] = Counter()
    facet_lookup = {facet.id: facet for facet in facets}
    adjacency: dict[str, list[tuple[str, float]]] = defaultdict(list)
    memory_paths: list[MemoryPath] = []

    for link in links:
        adjacency[link.source_id].append((link.target_id, float(link.weight or 0.0)))
        adjacency[link.target_id].append((link.source_id, float(link.weight or 0.0)))

    for facet in facets:
        score_by_facet[facet.id] += _facet_temporal_seed(facet, now=now)

    for event in story_river[:14]:
        related_ids = _infer_story_related_facet_ids(event, facets=facets)
        if not related_ids:
            continue
        anchor_score = (
            _story_event_temporal_weight(event.event_type)
            * _temporal_signal_weight(event.signal_kind)
            * max(0.18, _recency_score(event.happened_at_utc, now=now))
        )
        traversed: list[str] = []
        seen_neighbors: set[str] = set()
        for facet_id in related_ids:
            score_by_facet[facet_id] += anchor_score
            anchor_counts[facet_id] += 1
            if facet_id not in traversed:
                traversed.append(facet_id)
            for neighbor_id, weight in sorted(adjacency.get(facet_id, []), key=lambda item: item[1], reverse=True)[:TEMPORAL_NEIGHBOR_LIMIT]:
                if neighbor_id in seen_neighbors:
                    continue
                decay = anchor_score * max(0.24, min(0.82, weight)) * TEMPORAL_DECAY
                if decay < 0.08:
                    continue
                score_by_facet[neighbor_id] += decay
                seen_neighbors.add(neighbor_id)
                if neighbor_id not in traversed:
                    traversed.append(neighbor_id)
        related_titles = [facet_lookup[item_id].title for item_id in traversed if item_id in facet_lookup][:3]
        memory_paths.append(
            MemoryPath(
                id=event.id,
                title=f"{event.title} -> {', '.join(related_titles) if related_titles else 'current headspace'}",
                summary=_truncate(
                    f"{event.title} is still reverberating through {', '.join(related_titles) if related_titles else 'the current mental map'}."
                ),
                anchor_title=event.title,
                anchor_signal_kind=event.signal_kind,
                anchor_time_local=event.happened_at_local,
                path_score=round(anchor_score, 3),
                related_facet_ids=traversed[:6],
                provenance=event.event_type,
            )
        )

    ranked_nodes: list[CurrentHeadspaceNode] = []
    for facet in sorted(
        facets,
        key=lambda item: (
            score_by_facet.get(item.id, 0.0),
            item.attention_score,
            item.recency_score,
        ),
        reverse=True,
    ):
        if facet.facet_type not in CURRENT_HEADSPACE_FACET_TYPES:
            continue
        path_score = round(score_by_facet.get(facet.id, 0.0), 3)
        anchor_count = int(anchor_counts.get(facet.id, 0))
        if _is_low_signal_headspace_facet(facet):
            continue
        if path_score < _headspace_min_score(facet, anchor_count=anchor_count):
            continue
        if facet.facet_type in {"thoughts", "ideas"} and facet.recency_score < 0.28 and anchor_count == 0:
            continue
        if facet.facet_type == "projects" and facet.signal_kind == "derived_system" and anchor_count == 0 and facet.recency_score < 0.5:
            continue
        ranked_nodes.append(
            CurrentHeadspaceNode(
                facet_id=facet.id,
                title=facet.title,
                facet_type=facet.facet_type,
                summary=facet.summary,
                signal_kind=facet.signal_kind,
                happened_at_local=facet.happened_at_local,
                path_score=path_score,
                anchor_count=anchor_count,
                why_now=_headspace_reason(
                    facet=facet,
                    path_score=path_score,
                    anchor_count=anchor_count,
                ),
            )
        )
        if len(ranked_nodes) >= 12:
            break

    memory_paths.sort(key=lambda item: (item.path_score, item.anchor_time_local or ""), reverse=True)
    return ranked_nodes, memory_paths[:10], {key: round(value, 3) for key, value in score_by_facet.items()}


def _project_facet(project, snapshot, *, story: dict | None = None, now: datetime | None = None) -> BrainFacet:
    current_time = now or _utcnow()
    journal_entries = list((story or {}).get("journal_entries") or [])
    curated_entries = [
        entry
        for entry in journal_entries
        if (getattr(entry, "entry_type", "") or "") in CURATED_STORY_ENTRY_TYPES
        and _is_recent(getattr(entry, "happened_at", None), now=current_time, days=CURRENT_STORY_WINDOW_DAYS)
    ]
    latest_curated = curated_entries[0] if curated_entries else None
    recent_direct_entries = [
        entry
        for entry in curated_entries
        if signal_kind_for_event(
            entry_type=getattr(entry, "entry_type", None),
            actor_type=getattr(entry, "actor_type", None),
        )
        in {"direct_human", "direct_agent"}
    ]
    repos = list((story or {}).get("repos") or [])
    workspace_path = _preferred_workspace_path(repos)
    latest_summary = _truncate(
        getattr(latest_curated, "summary", None)
        or getattr(latest_curated, "outcome", None)
        or getattr(latest_curated, "title", None),
        limit=240,
    )
    summary = _truncate(
        latest_summary
        or snapshot.implemented
        or snapshot.what_changed
        or project.content
        or "Project state is still forming.",
        limit=240,
    )
    open_loops = _safe_list(
        [snapshot.remaining, *(snapshot.blockers or []), *(snapshot.holes or [])],
        limit=5,
    )
    evidence = [
        _make_evidence(
            title="Latest curated signal",
            summary=latest_summary,
            signal_kind=signal_kind_for_event(
                entry_type=getattr(latest_curated, "entry_type", None),
                actor_type=getattr(latest_curated, "actor_type", None),
            )
            if latest_curated
            else "derived_system",
            happened_at=getattr(latest_curated, "happened_at", None),
            metadata={"entry_type": getattr(latest_curated, "entry_type", None)},
        ),
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
    if workspace_path:
        evidence.append(
            _make_evidence(
                title="Current workspace",
                summary=workspace_path,
                signal_kind="direct_sync",
                happened_at=snapshot.last_signal_at or snapshot.updated_at,
            )
        )
    attention_score = round(
        min(
            1.0,
            float(snapshot.active_score or 0.0)
            + _current_headspace_bonus(
                summary=summary,
                title=project.title,
                happened_at=getattr(latest_curated, "happened_at", None) or snapshot.last_signal_at or snapshot.updated_at,
                signal_kind=signal_kind_for_event(
                    entry_type=getattr(latest_curated, "entry_type", None),
                    actor_type=getattr(latest_curated, "actor_type", None),
                )
                if latest_curated
                else ("direct_agent" if snapshot.manual_state == "pinned" else "derived_system"),
            ),
            + (0.08 if recent_direct_entries else -0.1),
            + (0.04 if workspace_path else -0.05),
        ),
        3,
    )
    return BrainFacet(
        id=f"facet:project:{project.id}",
        facet_type="projects",
        title=project.title,
        summary=summary,
        attention_score=attention_score,
        recency_score=round(
            _recency_score(getattr(latest_curated, "happened_at", None) or snapshot.last_signal_at or snapshot.updated_at, now=current_time),
            3,
        ),
        signal_kind=(
            signal_kind_for_event(
                entry_type=getattr(latest_curated, "entry_type", None),
                actor_type=getattr(latest_curated, "actor_type", None),
            )
            if latest_curated
            else ("direct_agent" if snapshot.manual_state == "pinned" else "derived_system")
        ),
        created_at_utc=_utc_iso(project.created_at),
        happened_at_utc=_utc_iso(getattr(latest_curated, "happened_at", None) or snapshot.last_signal_at or snapshot.updated_at),
        created_at_local=format_display_datetime(project.created_at),
        happened_at_local=format_display_datetime(getattr(latest_curated, "happened_at", None) or snapshot.last_signal_at or snapshot.updated_at),
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
            "workspace_path": workspace_path,
            "latest_curated_entry_type": getattr(latest_curated, "entry_type", None),
            "recent_direct_count": len(recent_direct_entries[:6]),
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


def _should_surface_note_facet(note) -> bool:
    title = str(note.title or "")
    content = str(note.content or "")
    if any(title.startswith(prefix) for prefix in ATLAS_EXCLUDED_NOTE_PREFIXES):
        return False
    if _low_signal_text_penalty(title) + _low_signal_text_penalty(content) >= 0.3:
        return False
    if note.category in {"note", "resource"} and not note.discord_message_id and _recency_score(note.updated_at or note.created_at) < 0.34:
        return False
    return True


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
    if signal_kind == "direct_agent":
        return None
    if any(str(title or "").startswith(prefix) for prefix in ATLAS_EXCLUDED_ARTIFACT_PREFIXES):
        return None
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


def _system_facets(sync_runs: list[dict], reviews: list, traces: list, eval_runs: list) -> list[BrainFacet]:
    by_source: dict[str, list[dict]] = defaultdict(list)
    for row in sync_runs:
        run = row["run"]
        source = row["sync_source"]
        by_source[str(source.id)].append({"run": run, "sync_source": source})

    facets: list[BrainFacet] = []
    for source_id, rows in list(by_source.items())[:6]:
        latest_row = sorted(rows, key=lambda item: item["run"].started_at, reverse=True)[0]
        latest = latest_row["run"]
        source = latest_row["sync_source"]
        facets.append(
            BrainFacet(
                id=f"facet:system:sync:{source_id}",
                facet_type="systems",
                title=f"{source.name}",
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
                metadata={"status": latest.status, "mode": latest.mode, "source_type": source.source_type},
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


def _interest_and_media_facets(chrome_rows: list[dict], *, now: datetime) -> tuple[list[BrainFacet], list[BrainFacet]]:
    interest_counts: dict[str, float] = defaultdict(float)
    interest_refs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    interest_latest: dict[str, datetime] = {}
    media_counts: dict[str, float] = defaultdict(float)
    media_refs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    media_latest: dict[str, datetime] = {}

    for row in chrome_rows:
        source_item = row["source_item"]
        payload = dict(source_item.payload or {})
        metadata = dict(payload.get("metadata") or {})
        title = source_item.title
        signal_time = source_item.happened_at or source_item.created_at
        recency_weight = _recency_score(signal_time, now=now)
        for theme in metadata.get("keyword_themes") or []:
            term = str(theme.get("term") or "").strip()
            if not term or term.lower() in KEYWORD_STOPWORDS:
                continue
            weighted_count = max(0.4, float(theme.get("count") or 1) * recency_weight)
            interest_counts[term] += weighted_count
            if signal_time and (
                term not in interest_latest
                or coerce_datetime(signal_time) > interest_latest[term]
            ):
                interest_latest[term] = coerce_datetime(signal_time) or now
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
                weighted_count = max(0.4, float(example.get("count") or 1) * recency_weight)
                media_counts[label] += weighted_count
                if signal_time and (
                    label not in media_latest
                    or coerce_datetime(signal_time) > media_latest[label]
                ):
                    media_latest[label] = coerce_datetime(signal_time) or now
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
            summary=f"Recurring interest signal across recent Chrome activity with {count:.1f} weighted mentions.",
            attention_score=round(
                min(0.95, 0.22 + count / 8 + _recency_score(interest_latest.get(term), now=now) * 0.18),
                3,
            ),
            recency_score=round(_recency_score(interest_latest.get(term), now=now), 3),
            signal_kind="direct_sync",
            created_at_utc=None,
            happened_at_utc=interest_refs[term][0]["event_time_utc"] if interest_refs[term] else None,
            created_at_local=None,
            happened_at_local=interest_refs[term][0]["happened_at_local"] if interest_refs[term] else None,
            display_timezone=display_timezone().key,
            evidence=interest_refs[term][:3],
            metadata={"count": round(count, 2), "latest_at": _utc_iso(interest_latest.get(term))},
        )
        for term, count in sorted(interest_counts.items(), key=lambda item: item[1], reverse=True)
        if count >= 1.2
    ]

    media_facets = [
        BrainFacet(
            id=f"facet:media:{_normalize_key(label)}",
            facet_type="media",
            title=label,
            summary=f"Media pattern surfacing repeatedly in recent Chrome activity with {count:.1f} weighted mentions.",
            attention_score=round(
                min(0.92, 0.22 + count / 8 + _recency_score(media_latest.get(label), now=now) * 0.18),
                3,
            ),
            recency_score=round(_recency_score(media_latest.get(label), now=now), 3),
            signal_kind="direct_sync",
            created_at_utc=None,
            happened_at_utc=media_refs[label][0]["event_time_utc"] if media_refs[label] else None,
            created_at_local=None,
            happened_at_local=media_refs[label][0]["happened_at_local"] if media_refs[label] else None,
            display_timezone=display_timezone().key,
            evidence=media_refs[label][:3],
            metadata={"count": round(count, 2), "latest_at": _utc_iso(media_latest.get(label))},
        )
        for label, count in sorted(media_counts.items(), key=lambda item: item[1], reverse=True)
        if count >= 1.2
    ]
    return interest_facets[:8], media_facets[:8]


def _story_river_entry_score(entry, *, now: datetime) -> float:
    entry_type = (getattr(entry, "entry_type", "") or "").lower()
    if entry_type in NOISY_STORY_ENTRY_TYPES or entry_type in DERIVED_ENTRY_TYPES:
        return 0.0
    base = {
        "progress_update": 1.0,
        "session_closeout": 0.96,
        "decision": 0.88,
        "conversation_session": 0.7,
    }.get(entry_type, 0.34)
    signal_kind = signal_kind_for_event(entry_type=getattr(entry, "entry_type", None), actor_type=getattr(entry, "actor_type", None))
    if signal_kind in {"direct_human", "direct_agent"}:
        base += 0.08
    elif signal_kind == "derived_system":
        base -= 0.2
    if _contains_legacy_workspace(getattr(entry, "summary", None)) and not _is_recent(getattr(entry, "happened_at", None), now=now, days=7):
        base -= 0.12
    base += _recency_score(getattr(entry, "happened_at", None), now=now) * 0.18
    return round(base, 3)


def _story_river_events(boards: list, recent_activity: list, *, now: datetime) -> list[StoryRiverEvent]:
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
                    "related_refs": [item.get("project") for item in list(payload.get("project_signals") or []) if item.get("project")][:5],
                    "score": round(0.82 + _recency_score(board.coverage_end, now=now) * 0.12, 3),
                },
            )
        )
    curated_entries = [
        entry
        for entry in recent_activity
        if _story_river_entry_score(entry, now=now) >= 0.48
    ]
    for entry in curated_entries[:20]:
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
                    "related_refs": [getattr(entry, "subject_ref", None)] if getattr(entry, "subject_ref", None) else [],
                    "score": _story_river_entry_score(entry, now=now),
                },
            )
        )
    events.sort(key=lambda item: item.happened_at_utc or "", reverse=True)
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
    now = _utcnow()
    project_snapshots = await store.list_project_state_snapshots(session, limit=20)
    notes = await store.list_notes(session, limit=140)
    artifacts = await store.list_artifact_interpretations(session, limit=140)
    recent_activity = await store.list_recent_activity(session, limit=140)
    boards = await store.list_boards(session, limit=18)
    chrome_rows = await store.list_source_items_with_sources(session, source_type="chrome_activity", limit=80)
    story_connections = await store.list_story_connections(session, limit=60)
    sync_runs = await store.list_recent_sync_runs_with_sources(session, limit=18)
    reviews = await store.get_pending_reviews(session)
    traces = await store.list_retrieval_traces(session, limit=18)
    eval_runs = await store.list_eval_runs(session, limit=8)
    library_preview = await build_library_items(session, limit=16)

    project_facets: list[BrainFacet] = []
    for snapshot in project_snapshots:
        project = await store.get_note(session, snapshot.project_note_id)
        if not project:
            continue
        story = await store.get_project_story(session, project.id)
        project_facets.append(_project_facet(project, snapshot, story=story, now=now))

    note_facets: list[BrainFacet] = []
    for note in notes:
        if note.category == "project":
            continue
        if note.category != "people" and not _is_recent(note.updated_at or note.created_at, now=now, days=CURRENT_HEADSPACE_WINDOW_DAYS):
            continue
        if not _should_surface_note_facet(note):
            continue
        if note.category in {"people", "idea", "note", "resource"}:
            note_facets.append(_note_facet(note))

    artifact_facets = [
        facet
        for facet in (
            _artifact_facet(item)
            for item in artifacts[:80]
            if _is_recent(item["artifact"].created_at, now=now, days=CURRENT_ARTIFACT_WINDOW_DAYS)
        )
        if facet
    ]
    interest_facets, media_facets = _interest_and_media_facets(chrome_rows, now=now)
    story_facets = [
        _story_facet_from_board(board)
        for board in boards[:6]
        if _is_recent(board.coverage_end or board.updated_at, now=now, days=CURRENT_STORY_WINDOW_DAYS)
    ]
    story_facets.extend(
        facet
        for facet in (
            _story_facet_from_entry(entry)
            for entry in recent_activity[:40]
            if _story_river_entry_score(entry, now=now) >= 0.58
        )
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
    facets = [
        facet
        for facet in facets
        if facet.facet_type in {"projects", "stories", "systems"}
        or facet.recency_score >= 0.25
        or facet.attention_score >= 0.52
        or facet.signal_kind in {"direct_human", "direct_agent"}
    ]

    story_river = _story_river_events(boards, recent_activity, now=now)
    links = _build_links(facets, story_connections)
    current_headspace, memory_paths, path_scores = _build_temporal_traversal(
        facets,
        story_river,
        links,
        now=now,
    )
    for facet in facets:
        facet.metadata["path_score"] = round(path_scores.get(facet.id, 0.0), 3)
        facet.metadata["in_current_headspace"] = any(node.facet_id == facet.id for node in current_headspace)
    facets.sort(
        key=lambda facet: (
            path_scores.get(facet.id, 0.0) * 0.72
            + facet.attention_score
            + facet.recency_score * 0.28
            + (0.1 if facet.signal_kind in {"direct_human", "direct_agent"} else 0.0),
            facet.recency_score,
        ),
        reverse=True,
    )
    facets = facets[:54]
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
        "current_headspace_count": len(current_headspace),
        "memory_path_count": len(memory_paths),
        "pending_review_count": len(reviews),
        "recent_trace_failures": len([trace for trace in traces if trace.failure_stage]),
        "latest_syncs": [
            {
                "source_id": str(row["run"].sync_source_id),
                "source_name": row["sync_source"].name,
                "source_type": row["sync_source"].source_type,
                "mode": row["run"].mode,
                "status": row["run"].status,
                "started_at_local": format_display_datetime(row["run"].started_at),
                "items_seen": row["run"].items_seen,
                "items_imported": row["run"].items_imported,
            }
            for row in sync_runs[:6]
        ],
    }

    return FacetSnapshot(
        generated_at_local=format_display_datetime(now),
        display_timezone=display_timezone().key,
        facets=facets,
        links=links,
        story_river=story_river,
        subconscious=subconscious,
        current_headspace=current_headspace,
        memory_paths=memory_paths,
        health=health,
        library_preview=library_preview,
    )
