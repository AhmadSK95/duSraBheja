"""Deterministic planner parsing and rollup helpers."""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timedelta

from src.constants import normalize_tags

_DATE_PATTERN = re.compile(
    r"(?P<label>(?:(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s*,?\s+)?"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+"
    r"\d{1,2}(?:st|nd|rd|th)?\s*,?\s+\d{4})",
    re.IGNORECASE,
)
_BULLET_PREFIX = re.compile(r"^(?:[\u2192•*\-]+|\d+[.)])\s*")
_DAY_NAME_PATTERN = re.compile(
    r"\b(mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday)?|thu(?:rs(?:day)?)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)\b",
    re.IGNORECASE,
)
_WEEK_SCOPE_PATTERN = re.compile(r"\b(weekly|week of|this week|next week)\b", re.IGNORECASE)
_DAY_SCOPE_PATTERN = re.compile(r"\b(today|daily|tomorrow|agenda|plan for today)\b", re.IGNORECASE)


def _clean_date_label(label: str) -> str:
    cleaned = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", label, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -:\u2192")
    return cleaned


def _parse_date_label(label: str) -> date | None:
    cleaned = _clean_date_label(label)
    for fmt in (
        "%A, %b %d, %Y",
        "%A %b %d, %Y",
        "%b %d, %Y",
        "%A, %B %d, %Y",
        "%A %B %d, %Y",
        "%B %d, %Y",
    ):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def extract_planner_dates(text: str, entities: list[dict] | None = None) -> list[dict]:
    seen: dict[str, dict] = {}

    for match in _DATE_PATTERN.finditer(text or ""):
        label = match.group("label").strip()
        parsed = _parse_date_label(label)
        if not parsed:
            continue
        seen.setdefault(parsed.isoformat(), {"label": label, "date": parsed})

    for entity in entities or []:
        if entity.get("type") != "date":
            continue
        label = str(entity.get("value") or "").strip()
        parsed = _parse_date_label(label)
        if not parsed:
            continue
        seen.setdefault(parsed.isoformat(), {"label": label, "date": parsed})

    return [
        {
            "label": payload["label"],
            "iso_date": iso_date,
            "display": payload["date"].strftime("%A, %b %d, %Y"),
        }
        for iso_date, payload in sorted(seen.items())
    ]


def detect_planner_scope(text: str, entities: list[dict] | None = None) -> str | None:
    dates = extract_planner_dates(text, entities)
    lowered = (text or "").lower()
    explicit_week = bool(_WEEK_SCOPE_PATTERN.search(lowered))
    explicit_day = bool(_DAY_SCOPE_PATTERN.search(lowered))

    seen_days: set[str] = set()
    for match in _DAY_NAME_PATTERN.finditer(lowered):
        seen_days.add(match.group(1)[:3].lower())

    if explicit_week or len(dates) >= 2 or len(seen_days) >= 2:
        return "weekly_planner"
    if len(dates) == 1 or len(seen_days) == 1 or explicit_day:
        return "daily_planner"
    return None


def _strip_item_prefix(line: str) -> str:
    return _BULLET_PREFIX.sub("", line.strip()).strip()


def _group_planner_items(text: str, dates: list[dict]) -> list[dict]:
    date_by_iso = {item["iso_date"]: item for item in dates}
    date_line_to_iso = {
        _clean_date_label(item["label"]).lower(): item["iso_date"]
        for item in dates
    }
    groups: list[dict] = []
    current_iso: str | None = None

    def _ensure_group(iso_date: str | None) -> dict:
        for group in groups:
            if group["iso_date"] == iso_date:
                return group
        label = date_by_iso[iso_date]["display"] if iso_date and iso_date in date_by_iso else "General"
        group = {"iso_date": iso_date, "label": label, "items": []}
        groups.append(group)
        return group

    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue

        matched_date = _DATE_PATTERN.search(line)
        if matched_date:
            iso_date = None
            parsed = _parse_date_label(matched_date.group("label"))
            if parsed:
                iso_date = parsed.isoformat()
            normalized = _clean_date_label(matched_date.group("label")).lower()
            current_iso = date_line_to_iso.get(normalized, iso_date)
            _ensure_group(current_iso)
            continue

        item = _strip_item_prefix(line)
        if not item or item.startswith("#"):
            continue

        group = _ensure_group(current_iso)
        if group["items"] and raw_line[:1].isspace() and len(item.split()) < 8:
            group["items"][-1] = f"{group['items'][-1]} {item}".strip()
            continue
        if item not in group["items"]:
            group["items"].append(item)

    return [group for group in groups if group["items"]]


def _unique_entity_values(entities: list[dict], entity_type: str) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for entity in entities:
        if entity.get("type") != entity_type:
            continue
        value = str(entity.get("value") or "").strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        values.append(value)
    return values


def _title_for_planner(category: str, dates: list[dict]) -> str:
    if category == "weekly_planner":
        if dates:
            first_date = datetime.strptime(dates[0]["iso_date"], "%Y-%m-%d").date()
            week_start = first_date - timedelta(days=first_date.weekday())
            return f"Week of {week_start.strftime('%b %d, %Y')}"
        return "Weekly Planner"
    if dates:
        return f"Daily Planner: {dates[0]['display']}"
    return "Daily Planner"


def _summary_for_planner(category: str, dates: list[dict], top_items: list[str], fallback_summary: str) -> str:
    if fallback_summary:
        return fallback_summary
    if dates and top_items:
        scope = "week" if category == "weekly_planner" or len(dates) > 2 else "day"
        return f"Planner capture for this {scope} with {len(top_items)} tracked items."
    if dates:
        return f"Planner capture covering {len(dates)} date entries."
    return "Planner capture stored in the brain."


def _render_planner_content(
    title: str,
    summary: str,
    dates: list[dict],
    groups: list[dict],
    projects: list[str],
    people: list[str],
    raw_text: str,
) -> str:
    lines = [f"# {title}", "", "## Summary", summary]

    if dates:
        lines.extend(["", "## Dates"])
        lines.extend(f"- {item['display']}" for item in dates)

    if projects:
        lines.extend(["", "## Focus Projects"])
        lines.extend(f"- {project}" for project in projects[:10])

    if people:
        lines.extend(["", "## People"])
        lines.extend(f"- {person}" for person in people[:10])

    if groups:
        lines.extend(["", "## Planned Items"])
        for group in groups:
            lines.extend(["", f"### {group['label']}"])
            lines.extend(f"- {item}" for item in group["items"][:20])

    lines.extend(["", "## Raw Capture", "```text", (raw_text or "").strip()[:6000], "```"])
    return "\n".join(lines).strip()


def build_planner_payload(raw_text: str, classification: dict, fallback_summary: str = "") -> dict:
    category = classification["category"]
    entities = list(classification.get("entities") or [])
    dates = extract_planner_dates(raw_text, entities)
    groups = _group_planner_items(raw_text, dates)
    top_items: list[str] = []
    for group in groups:
        for item in group["items"]:
            if item not in top_items:
                top_items.append(item)

    projects = _unique_entity_values(entities, "project")
    people = _unique_entity_values(entities, "person")
    title = _title_for_planner(category, dates)
    summary = _summary_for_planner(category, dates, top_items, fallback_summary)
    content = _render_planner_content(title, summary, dates, groups, projects, people, raw_text)

    week_start = None
    if dates:
        first_date = datetime.strptime(dates[0]["iso_date"], "%Y-%m-%d").date()
        week_start = first_date - timedelta(days=first_date.weekday())

    tags = normalize_tags(
        [
            *(classification.get("tags") or []),
            "planner",
            category.replace("_", "-"),
            *(project.lower().replace(" ", "-") for project in projects[:5]),
        ]
    )

    return {
        "title": title,
        "content": content,
        "summary": summary,
        "tags": tags,
        "metadata": {
            "planner_dates": dates,
            "planner_groups": groups,
            "planner_top_items": top_items[:12],
            "planner_projects": projects[:10],
            "planner_people": people[:10],
            "week_start": week_start.isoformat() if week_start else None,
        },
        "card": {
            "title": title,
            "summary": summary,
            "dates": [item["display"] for item in dates[:7]],
            "top_items": top_items[:6],
            "focus_projects": projects[:5],
            "focus_people": people[:5],
            "week_start": week_start.isoformat() if week_start else None,
        },
    }


def merge_weekly_rollup(
    existing_metadata: dict | None,
    planner_payload: dict,
    artifact_id: uuid.UUID,
) -> tuple[dict, bool]:
    week_start = planner_payload["metadata"].get("week_start")
    if not week_start:
        return {}, False

    metadata = dict(existing_metadata or {})
    rollup = dict(metadata.get("planner_rollup") or {})
    artifact_ids = set(rollup.get("artifact_ids") or [])
    if str(artifact_id) in artifact_ids:
        return {
            "title": f"Week of {datetime.strptime(week_start, '%Y-%m-%d').strftime('%b %d, %Y')}",
            "content": _render_weekly_rollup_content(week_start, rollup.get("entries") or {}),
            "metadata": metadata,
            "tags": normalize_tags(["planner", "weekly-planner"]),
            "card": _build_weekly_rollup_card(week_start, rollup.get("entries") or {}),
        }, False

    entries = {key: dict(value) for key, value in (rollup.get("entries") or {}).items()}
    for group in planner_payload["metadata"].get("planner_groups") or []:
        if not group.get("items"):
            continue
        entry_key = group.get("iso_date") or "undated"
        entry = dict(entries.get(entry_key) or {"label": group["label"], "items": []})
        for item in group["items"]:
            if item not in entry["items"]:
                entry["items"].append(item)
        entries[entry_key] = entry

    artifact_ids.add(str(artifact_id))
    rollup = {
        "week_start": week_start,
        "artifact_ids": sorted(artifact_ids),
        "entries": entries,
    }
    metadata["planner_rollup"] = rollup
    metadata["planner_dates"] = planner_payload["metadata"].get("planner_dates") or []

    return {
        "title": f"Week of {datetime.strptime(week_start, '%Y-%m-%d').strftime('%b %d, %Y')}",
        "content": _render_weekly_rollup_content(week_start, entries),
        "metadata": metadata,
        "tags": normalize_tags(["planner", "weekly-planner"]),
        "card": _build_weekly_rollup_card(week_start, entries),
    }, True


def _render_weekly_rollup_content(week_start: str, entries: dict) -> str:
    title = f"Week of {datetime.strptime(week_start, '%Y-%m-%d').strftime('%b %d, %Y')}"
    lines = [f"# {title}", "", "## Daily Rollup"]

    for key in sorted(entries):
        entry = entries[key]
        lines.extend(["", f"### {entry['label']}"])
        lines.extend(f"- {item}" for item in entry["items"][:20])

    return "\n".join(lines).strip()


def _build_weekly_rollup_card(week_start: str, entries: dict) -> dict:
    ordered_entries = [entries[key] for key in sorted(entries)]
    top_items: list[str] = []
    for entry in ordered_entries:
        for item in entry["items"]:
            if item not in top_items:
                top_items.append(item)

    return {
        "title": f"Week of {datetime.strptime(week_start, '%Y-%m-%d').strftime('%b %d, %Y')}",
        "summary": f"Weekly rollup updated with {len(ordered_entries)} day sections.",
        "dates": [entry["label"] for entry in ordered_entries[:7]],
        "top_items": top_items[:6],
    }
