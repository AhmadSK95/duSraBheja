"""Curated Chrome signal distillation for a single profile."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import httpx

from src.config import settings
from src.database import async_session
from src.lib import store

CHROME_ROOT = Path("~/Library/Application Support/Google/Chrome").expanduser()
CHROME_LOCAL_STATE = CHROME_ROOT / "Local State"
STATIC_ASSET_SUFFIXES = {
    ".css",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".json",
    ".m3u8",
    ".map",
    ".mp4",
    ".png",
    ".svg",
    ".ts",
    ".txt",
    ".webp",
    ".woff",
    ".woff2",
    ".xml",
}
DEFAULT_EXCLUDED_DOMAINS = {
    "127.0.0.1",
    "accounts.google.com",
    "appleid.apple.com",
    "auth.openai.com",
    "bankofamerica.com",
    "capitalone.com",
    "chase.com",
    "discord.com",
    "localhost",
    "mail.google.com",
    "myaccount.google.com",
    "paypal.com",
    "takeout.google.com",
    "venmo.com",
    "wellsfargo.com",
}
DEFAULT_EXCLUDED_URL_PATTERNS = (
    "chrome-extension://",
    "chrome://",
    "edge://",
    "file://",
    "oauth",
    "signin",
    "sign-in",
    "login",
    "log-in",
    "logout",
    "log-out",
    "serviceworker",
)
OTT_DOMAINS = {
    "crunchyroll.com",
    "disneyplus.com",
    "hulu.com",
    "hotstar.com",
    "max.com",
    "netflix.com",
    "peacocktv.com",
    "primevideo.com",
    "tv.apple.com",
    "youtube.com/tv",
}
JOB_DOMAINS = {
    "codesignal.com",
    "glassdoor.com",
    "hackerrank.com",
    "indeed.com",
    "interviewing.io",
    "leetcode.com",
    "linkedin.com",
    "pramp.com",
    "wellfound.com",
}
SCHEDULING_DOMAINS = {
    "acuityscheduling.com",
    "calendly.com",
    "calendar.google.com",
    "meet.google.com",
    "simplepractice.com",
    "video.simplepractice.com",
    "zoom.us",
}
SHOPPING_ADMIN_DOMAINS = {
    "amazon.com",
    "costco.com",
    "instacart.com",
    "irs.gov",
    "target.com",
    "walmart.com",
}
PROJECT_WORK_DOMAINS = {
    "anthropic.com",
    "balkan.thisisrikisart.com",
    "cloud.google.com",
    "databricks.com",
    "github.com",
    "huggingface.co",
    "kaffaespressobar.com",
    "modal.com",
    "openai.com",
    "pinecone.io",
    "replicate.com",
}
GENERIC_STOPWORDS = {
    "about",
    "after",
    "again",
    "against",
    "all",
    "also",
    "am",
    "and",
    "any",
    "are",
    "bar",
    "because",
    "been",
    "before",
    "being",
    "between",
    "brain",
    "browser",
    "can",
    "company",
    "done",
    "from",
    "game",
    "get",
    "google",
    "history",
    "how",
    "into",
    "jobs",
    "latest",
    "made",
    "more",
    "much",
    "need",
    "new",
    "not",
    "now",
    "one",
    "our",
    "out",
    "over",
    "page",
    "project",
    "same",
    "search",
    "that",
    "the",
    "their",
    "them",
    "they",
    "this",
    "through",
    "today",
    "using",
    "video",
    "was",
    "what",
    "when",
    "where",
    "which",
    "with",
    "work",
    "youtube",
    "your",
}
GENERIC_ALIAS_TERMS = {
    "active",
    "app",
    "brain",
    "default",
    "main",
    "note",
    "notes",
    "overview",
    "project",
    "projects",
    "work",
}
JOB_HINT_RE = re.compile(r"\b(job|jobs|career|careers|interview|resume|hiring|application|applications)\b", re.I)
ADMIN_HINT_RE = re.compile(r"\b(tax|insurance|form|forms|payment|bill|billing|recipe|groceries|grocery)\b", re.I)
WORK_HINT_RE = re.compile(r"\b(api|deploy|deployment|database|discord|llm|mcp|postgres|rag|redis|vector)\b", re.I)
TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9\-_]{2,}")
MIN_GENERAL_BROWSING_LABEL_REPEAT = 2
CHROME_INGEST_BATCH_SIZE = 8


@dataclass(slots=True)
class ChromeProfile:
    directory: str
    display_name: str
    email: str
    gaia_name: str
    history_path: Path


@dataclass(slots=True)
class ChromeVisit:
    url: str
    title: str
    domain: str
    visited_at_utc: datetime
    visited_at_local: datetime


@dataclass(slots=True)
class SignalRecord:
    bucket: str
    label: str
    normalized_label: str
    domain: str
    url: str
    title: str
    visited_at_utc: datetime
    visited_at_local: datetime
    project_refs: tuple[str, ...]


def _profile_payload(profile: ChromeProfile) -> dict[str, str]:
    return {
        "directory": profile.directory,
        "display_name": profile.display_name,
        "email": profile.email,
        "gaia_name": profile.gaia_name,
        "history_path": str(profile.history_path),
    }


def _display_tz() -> ZoneInfo:
    return ZoneInfo(settings.digest_timezone)


def _parse_csv(raw_value: str | None) -> set[str]:
    return {item.strip().lower() for item in (raw_value or "").split(",") if item.strip()}


def _normalize_hostname(value: str) -> str:
    cleaned = (value or "").lower().strip()
    if cleaned.startswith("www."):
        cleaned = cleaned[4:]
    return cleaned.split(":", 1)[0]


def _expand(path: str | Path) -> Path:
    return Path(path).expanduser()


def _copy_db(path: Path) -> Path | None:
    if not path.exists():
        return None
    fd, tmp_path = tempfile.mkstemp(suffix=path.suffix)
    os.close(fd)
    try:
        shutil.copy2(path, tmp_path)
    except PermissionError:
        Path(tmp_path).unlink(missing_ok=True)
        return None
    return Path(tmp_path)


def _chromium_to_datetime(value: int) -> datetime:
    return datetime(1601, 1, 1, tzinfo=UTC) + timedelta(microseconds=value)


def _local_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=_display_tz())


def _local_end(day: date) -> datetime:
    return datetime.combine(day, time.max, tzinfo=_display_tz())


def _utc_from_local(local_dt: datetime) -> datetime:
    return local_dt.astimezone(UTC)


def _query_value(url: str) -> tuple[str | None, str | None]:
    parsed = urlparse(url)
    host = _normalize_hostname(parsed.netloc)
    if "google." in host and parsed.path == "/search":
        return "search_query", parse_qs(parsed.query).get("q", [None])[0]
    if host == "bing.com" and parsed.path == "/search":
        return "search_query", parse_qs(parsed.query).get("q", [None])[0]
    if host == "duckduckgo.com":
        return "search_query", parse_qs(parsed.query).get("q", [None])[0]
    if host == "youtube.com" and parsed.path == "/results":
        return "youtube_search", parse_qs(parsed.query).get("search_query", [None])[0]
    return None, None


def _clean_title(title: str) -> str:
    cleaned = re.sub(r"\s*[-|]\s*YouTube\s*$", "", (title or "").strip(), flags=re.I)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _normalize_label(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _tokenize(value: str) -> list[str]:
    tokens = []
    for token in TOKEN_RE.findall((value or "").lower()):
        if token in GENERIC_STOPWORDS:
            continue
        if token.isdigit():
            continue
        tokens.append(token)
    return tokens


def _title_or_domain(title: str, domain: str) -> str:
    cleaned = _clean_title(title)
    return cleaned or domain


def _is_nav_only(host: str, path: str, title: str) -> bool:
    if host == "google.com" and path in {"", "/"}:
        return True
    if host == "youtube.com" and path in {"", "/", "/feed/subscriptions", "/feed/history", "/feed/library", "/feed/you"}:
        return True
    if host == "youtube.com" and _clean_title(title).lower() in {"youtube", "subscriptions - youtube"}:
        return True
    return False


def _domain_matches(host: str, candidates: set[str]) -> bool:
    return any(host == item or host.endswith(f".{item}") for item in candidates)


def _period_key_for_visit(local_day: date, *, recent_cutoff: date) -> tuple[str, date]:
    if local_day >= recent_cutoff:
        week_start = local_day - timedelta(days=local_day.weekday())
        return "weekly", week_start
    month_start = local_day.replace(day=1)
    return "monthly", month_start


def _period_bounds(kind: str, start: date) -> tuple[date, date]:
    if kind == "weekly":
        return start, start + timedelta(days=6)
    if start.month == 12:
        next_month = start.replace(year=start.year + 1, month=1, day=1)
    else:
        next_month = start.replace(month=start.month + 1, day=1)
    return start, next_month - timedelta(days=1)


def _period_label(kind: str, start: date, end: date) -> str:
    if kind == "daily":
        return start.strftime("%b %-d, %Y")
    if kind == "weekly":
        return f"Week of {start.strftime('%b %-d, %Y')} to {end.strftime('%b %-d, %Y')}"
    return start.strftime("%B %Y")


def _content_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _chunk_entries(entries: list[dict[str, Any]], batch_size: int) -> list[list[dict[str, Any]]]:
    if batch_size <= 0:
        return [entries]
    return [entries[index : index + batch_size] for index in range(0, len(entries), batch_size)]


def resolve_chrome_profile(
    *,
    profile_email: str | None = None,
    profile_name: str | None = None,
    local_state_path: Path = CHROME_LOCAL_STATE,
    chrome_root: Path = CHROME_ROOT,
) -> ChromeProfile:
    if not local_state_path.exists():
        raise RuntimeError(f"Chrome Local State not found at {local_state_path}")
    data = json.loads(local_state_path.read_text(encoding="utf-8"))
    info_cache = data.get("profile", {}).get("info_cache", {})

    preferred_email = (profile_email or settings.chrome_signal_profile_email).strip().lower()
    preferred_name = (profile_name or settings.chrome_signal_profile_name).strip().lower()
    chosen_key: str | None = None
    chosen_value: dict[str, Any] | None = None

    for key, value in info_cache.items():
        email = (value.get("user_name") or "").strip().lower()
        name = (value.get("name") or "").strip().lower()
        gaia_name = (value.get("gaia_name") or "").strip().lower()
        if preferred_email and email == preferred_email:
            chosen_key, chosen_value = key, value
            break
        if preferred_name and preferred_name in {name, gaia_name}:
            chosen_key, chosen_value = key, value

    if not chosen_key or not chosen_value:
        raise RuntimeError(f"Could not find a Chrome profile for {preferred_email or preferred_name}")

    history_path = chrome_root / chosen_key / "History"
    if not history_path.exists():
        raise RuntimeError(f"Chrome History DB not found for profile {chosen_key}")

    return ChromeProfile(
        directory=chosen_key,
        display_name=chosen_value.get("name") or chosen_key,
        email=chosen_value.get("user_name") or preferred_email,
        gaia_name=chosen_value.get("gaia_name") or chosen_value.get("name") or chosen_key,
        history_path=history_path,
    )


def collect_chrome_visits(
    profile: ChromeProfile,
    *,
    start_utc: datetime | None = None,
    end_utc: datetime | None = None,
) -> list[ChromeVisit]:
    copied = _copy_db(profile.history_path)
    if not copied:
        return []

    clauses = []
    params: list[int] = []
    chromium_epoch = datetime(1601, 1, 1, tzinfo=UTC)
    if start_utc:
        start_value = int((start_utc.astimezone(UTC) - chromium_epoch).total_seconds() * 1_000_000)
        clauses.append("visits.visit_time >= ?")
        params.append(start_value)
    if end_utc:
        end_value = int((end_utc.astimezone(UTC) - chromium_epoch).total_seconds() * 1_000_000)
        clauses.append("visits.visit_time < ?")
        params.append(end_value)
    where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    visits: list[ChromeVisit] = []
    conn = sqlite3.connect(copied)
    try:
        cursor = conn.execute(
            f"""
            SELECT urls.url, urls.title, visits.visit_time
            FROM visits
            JOIN urls ON visits.url = urls.id
            {where_clause}
            ORDER BY visits.visit_time ASC
            """,
            params,
        )
        for url, title, visit_time in cursor:
            if not url:
                continue
            host = _normalize_hostname(urlparse(url).netloc)
            visited_at_utc = _chromium_to_datetime(int(visit_time))
            visits.append(
                ChromeVisit(
                    url=url,
                    title=title or "",
                    domain=host,
                    visited_at_utc=visited_at_utc,
                    visited_at_local=visited_at_utc.astimezone(_display_tz()),
                )
            )
    finally:
        conn.close()
        copied.unlink(missing_ok=True)

    return visits


async def load_project_alias_map() -> dict[str, str]:
    api_alias_map = await _load_project_alias_map_from_api()
    if api_alias_map:
        return api_alias_map

    alias_map: dict[str, str] = {}
    try:
        async with async_session() as session:
            projects = await store.list_project_notes(session, limit=200)
            titles_by_id = {str(project.id): project.title for project in projects}
            for project in projects:
                candidates = {project.title}
                for value in list(candidates):
                    lowered = value.lower()
                    if lowered.endswith(" - project overview"):
                        candidates.add(value[: -len(" - Project Overview")])
                for candidate in candidates:
                    for term in _alias_terms(candidate):
                        alias_map.setdefault(term, project.title)

            aliases = await store.list_project_aliases(session, limit=500)
            for alias in aliases:
                project_title = titles_by_id.get(str(alias.project_note_id))
                if not project_title:
                    continue
                for term in _alias_terms(alias.alias):
                    alias_map.setdefault(term, project_title)
    except Exception:
        return {}
    return alias_map


async def _load_project_alias_map_from_api() -> dict[str, str]:
    if not settings.api_token or not settings.collector_api_base_url:
        return {}
    try:
        async with httpx.AsyncClient(base_url=settings.collector_api_base_url, timeout=20) as client:
            response = await client.get(
                "/api/dashboard/project-aliases",
                headers={"Authorization": f"Bearer {settings.api_token}"},
            )
            response.raise_for_status()
            rows = response.json()
    except Exception:
        return {}

    alias_map: dict[str, str] = {}
    for row in rows:
        project_title = str(row.get("project_title") or "").strip()
        if not project_title:
            continue
        for alias in row.get("aliases") or []:
            for term in _alias_terms(alias):
                alias_map.setdefault(term, project_title)
    return alias_map


def _alias_terms(alias: str | None) -> set[str]:
    raw = (alias or "").strip()
    lowered = raw.lower()
    if not lowered:
        return set()
    variants = {
        lowered,
        lowered.replace("_", " "),
        lowered.replace("-", " "),
    }
    if lowered.endswith(" - project overview"):
        variants.add(lowered[: -len(" - project overview")])

    results = set()
    for value in variants:
        cleaned = re.sub(r"\s+", " ", value).strip(" -_/")
        if len(cleaned) < 4 or cleaned in GENERIC_ALIAS_TERMS:
            continue
        results.add(cleaned)
    return results


def _match_project_refs(text: str, alias_map: dict[str, str]) -> tuple[str, ...]:
    lowered = f" {text.lower()} "
    matches = []
    seen = set()
    for term, project_title in alias_map.items():
        if len(term) < 4:
            continue
        if f" {term} " in lowered or term in lowered:
            if project_title not in seen:
                seen.add(project_title)
                matches.append(project_title)
    return tuple(matches[:3])


def classify_visit(
    visit: ChromeVisit,
    *,
    alias_map: dict[str, str],
    excluded_domains: set[str],
    excluded_patterns: set[str],
) -> tuple[SignalRecord | None, str | None]:
    parsed = urlparse(visit.url)
    scheme = (parsed.scheme or "").lower()
    host = visit.domain
    path = (parsed.path or "").lower()
    full_url = visit.url.lower()
    label = _title_or_domain(visit.title, host)

    if scheme not in {"http", "https"}:
        return None, "browser_internal"
    if not host:
        return None, "missing_domain"
    if _domain_matches(host, excluded_domains):
        return None, "sensitive_or_excluded_domain"
    if any(pattern in full_url for pattern in excluded_patterns):
        return None, "excluded_url_pattern"
    if host in {"127.0.0.1", "localhost"} or host.endswith(".local"):
        return None, "local_noise"
    if Path(path).suffix.lower() in STATIC_ASSET_SUFFIXES:
        return None, "static_asset"
    if _is_nav_only(host, path, visit.title):
        return None, "navigation_only"

    bucket, query = _query_value(visit.url)
    cleaned_title = _clean_title(visit.title)
    match_text = " ".join(part for part in [visit.url, cleaned_title, query] if part)
    project_refs = _match_project_refs(match_text, alias_map)

    if bucket == "search_query" and query:
        return (
            SignalRecord(
                bucket="search_query",
                label=query.strip(),
                normalized_label=_normalize_label(query),
                domain=host,
                url=visit.url,
                title=cleaned_title,
                visited_at_utc=visit.visited_at_utc,
                visited_at_local=visit.visited_at_local,
                project_refs=project_refs,
            ),
            None,
        )
    if bucket == "youtube_search" and query:
        return (
            SignalRecord(
                bucket="youtube_search",
                label=query.strip(),
                normalized_label=_normalize_label(query),
                domain=host,
                url=visit.url,
                title=cleaned_title,
                visited_at_utc=visit.visited_at_utc,
                visited_at_local=visit.visited_at_local,
                project_refs=project_refs,
            ),
            None,
        )
    if (host == "youtube.com" and path == "/watch") or host == "youtu.be":
        record_label = cleaned_title or visit.url
        return (
            SignalRecord(
                bucket="youtube_watch",
                label=record_label,
                normalized_label=_normalize_label(record_label),
                domain=host,
                url=visit.url,
                title=cleaned_title,
                visited_at_utc=visit.visited_at_utc,
                visited_at_local=visit.visited_at_local,
                project_refs=project_refs,
            ),
            None,
        )
    if _domain_matches(host, OTT_DOMAINS):
        record_label = cleaned_title or host
        return (
            SignalRecord(
                bucket="ott_visit",
                label=record_label,
                normalized_label=_normalize_label(record_label),
                domain=host,
                url=visit.url,
                title=cleaned_title,
                visited_at_utc=visit.visited_at_utc,
                visited_at_local=visit.visited_at_local,
                project_refs=project_refs,
            ),
            None,
        )
    if _domain_matches(host, JOB_DOMAINS) or JOB_HINT_RE.search(f"{cleaned_title} {query or ''} {visit.url}"):
        record_label = query or cleaned_title or host
        return (
            SignalRecord(
                bucket="job_or_interview",
                label=record_label,
                normalized_label=_normalize_label(record_label),
                domain=host,
                url=visit.url,
                title=cleaned_title,
                visited_at_utc=visit.visited_at_utc,
                visited_at_local=visit.visited_at_local,
                project_refs=project_refs,
            ),
            None,
        )
    if project_refs or _domain_matches(host, PROJECT_WORK_DOMAINS) or WORK_HINT_RE.search(f"{cleaned_title} {query or ''}"):
        record_label = query or cleaned_title or host
        return (
            SignalRecord(
                bucket="project_or_work_research",
                label=record_label,
                normalized_label=_normalize_label(record_label),
                domain=host,
                url=visit.url,
                title=cleaned_title,
                visited_at_utc=visit.visited_at_utc,
                visited_at_local=visit.visited_at_local,
                project_refs=project_refs,
            ),
            None,
        )
    if _domain_matches(host, SCHEDULING_DOMAINS):
        record_label = cleaned_title or host
        return (
            SignalRecord(
                bucket="scheduling_or_comms",
                label=record_label,
                normalized_label=_normalize_label(record_label),
                domain=host,
                url=visit.url,
                title=cleaned_title,
                visited_at_utc=visit.visited_at_utc,
                visited_at_local=visit.visited_at_local,
                project_refs=project_refs,
            ),
            None,
        )
    if _domain_matches(host, SHOPPING_ADMIN_DOMAINS) or ADMIN_HINT_RE.search(f"{cleaned_title} {query or ''}"):
        record_label = query or cleaned_title or host
        return (
            SignalRecord(
                bucket="shopping_or_admin",
                label=record_label,
                normalized_label=_normalize_label(record_label),
                domain=host,
                url=visit.url,
                title=cleaned_title,
                visited_at_utc=visit.visited_at_utc,
                visited_at_local=visit.visited_at_local,
                project_refs=project_refs,
            ),
            None,
        )
    return (
        SignalRecord(
            bucket="general_browsing",
            label=cleaned_title or host,
            normalized_label=_normalize_label(cleaned_title or host),
            domain=host,
            url=visit.url,
            title=cleaned_title,
            visited_at_utc=visit.visited_at_utc,
            visited_at_local=visit.visited_at_local,
            project_refs=project_refs,
        ),
        None,
    )


def _top_examples(records: list[SignalRecord], limit: int) -> list[dict[str, Any]]:
    by_label: dict[str, dict[str, Any]] = {}
    for record in records:
        current = by_label.get(record.normalized_label)
        if not current:
            by_label[record.normalized_label] = {
                "label": record.label,
                "count": 1,
                "latest_at_local": record.visited_at_local.isoformat(),
                "domain": record.domain,
                "project_refs": list(record.project_refs),
            }
            continue
        current["count"] += 1
        if record.visited_at_local.isoformat() > current["latest_at_local"]:
            current["latest_at_local"] = record.visited_at_local.isoformat()
        for project_ref in record.project_refs:
            if project_ref not in current["project_refs"]:
                current["project_refs"].append(project_ref)
    ranked = sorted(by_label.values(), key=lambda item: (item["count"], item["latest_at_local"]), reverse=True)
    return ranked[:limit]


def _format_key_value_lines(items: list[tuple[str, Any]]) -> list[str]:
    if not items:
        return ["- none"]
    return [f"- {key}: {value}" for key, value in items]


def _format_project_lines(items: list[dict[str, Any]]) -> list[str]:
    if not items:
        return ["- none"]
    return [f"- {item['project_ref']} ({item['count']} signals)" for item in items]


def _top_keywords(values: list[str], limit: int = 8) -> list[dict[str, Any]]:
    counts = Counter(token for value in values for token in _tokenize(value))
    return [{"term": term, "count": count} for term, count in counts.most_common(limit)]


def _should_keep_general_browsing(
    record: SignalRecord,
    *,
    label_counts: Counter[str],
) -> bool:
    label_repeat = label_counts.get(record.normalized_label, 0)
    meaningful_tokens = _tokenize(record.label)
    if label_repeat >= MIN_GENERAL_BROWSING_LABEL_REPEAT and len(meaningful_tokens) >= 3:
        return True
    return False


def analyze_records(
    visits: list[ChromeVisit],
    *,
    alias_map: dict[str, str],
) -> dict[str, Any]:
    excluded_domains = DEFAULT_EXCLUDED_DOMAINS | _parse_csv(settings.chrome_signal_excluded_domains)
    excluded_patterns = {pattern.lower() for pattern in DEFAULT_EXCLUDED_URL_PATTERNS} | _parse_csv(
        settings.chrome_signal_excluded_url_patterns
    )

    provisional: list[SignalRecord] = []
    excluded_counts: Counter[str] = Counter()

    for visit in visits:
        record, excluded_reason = classify_visit(
            visit,
            alias_map=alias_map,
            excluded_domains=excluded_domains,
            excluded_patterns=excluded_patterns,
        )
        if excluded_reason:
            excluded_counts[excluded_reason] += 1
            continue
        if not record:
            continue
        provisional.append(record)

    general_label_counts = Counter(
        record.normalized_label for record in provisional if record.bucket == "general_browsing"
    )
    kept: list[SignalRecord] = []
    bucket_counts: Counter[str] = Counter()
    project_counts: Counter[str] = Counter()
    bucket_records: dict[str, list[SignalRecord]] = defaultdict(list)

    for record in provisional:
        if record.bucket == "general_browsing" and not _should_keep_general_browsing(
            record,
            label_counts=general_label_counts,
        ):
            excluded_counts["low_signal_general_browsing"] += 1
            continue
        kept.append(record)
        bucket_counts[record.bucket] += 1
        bucket_records[record.bucket].append(record)
        for project_ref in record.project_refs:
            project_counts[project_ref] += 1

    search_examples = _top_examples(bucket_records["search_query"], settings.chrome_signal_max_exemplars_per_signal)
    youtube_search_examples = _top_examples(
        bucket_records["youtube_search"], settings.chrome_signal_max_exemplars_per_signal
    )
    youtube_watch_examples = _top_examples(
        bucket_records["youtube_watch"], settings.chrome_signal_max_exemplars_per_signal
    )
    ott_examples = _top_examples(bucket_records["ott_visit"], settings.chrome_signal_max_exemplars_per_signal)
    job_examples = _top_examples(bucket_records["job_or_interview"], settings.chrome_signal_max_exemplars_per_signal)
    work_examples = _top_examples(
        bucket_records["project_or_work_research"], settings.chrome_signal_max_exemplars_per_signal
    )
    scheduling_examples = _top_examples(
        bucket_records["scheduling_or_comms"], settings.chrome_signal_max_exemplars_per_signal
    )
    admin_examples = _top_examples(
        bucket_records["shopping_or_admin"], settings.chrome_signal_max_exemplars_per_signal
    )

    project_entries: list[dict[str, Any]] = []
    for project_ref, count in project_counts.most_common(8):
        related = [record for record in kept if project_ref in record.project_refs]
        project_entries.append(
            {
                "project_ref": project_ref,
                "count": count,
                "examples": _top_examples(related, settings.chrome_signal_max_exemplars_per_signal),
                "bucket_counts": Counter(record.bucket for record in related),
            }
        )

    action_items: list[str] = []
    if job_examples:
        action_items.append("Job and interview activity is active in Chrome and should stay visible in the brain.")
    if scheduling_examples:
        action_items.append("Scheduling and coordination load showed up; these events may explain context switching.")
    if project_entries:
        action_items.append(
            "Project-adjacent browsing appeared for " + ", ".join(item["project_ref"] for item in project_entries[:3]) + "."
        )
    repeated_searches = [item["label"] for item in search_examples if item["count"] >= 3][:3]
    if repeated_searches:
        action_items.append("Repeated searches suggest unresolved threads: " + ", ".join(repeated_searches) + ".")
    if not action_items:
        action_items.append("Chrome activity was present, but most of it reduced to light browsing rather than strong action signals.")

    confidence = 0.92 if kept else 0.35
    if len(kept) < 10:
        confidence = min(confidence, 0.7)

    return {
        "total_visits": len(visits),
        "kept_visits": len(kept),
        "excluded_counts": dict(excluded_counts),
        "bucket_counts": dict(bucket_counts),
        "search_examples": search_examples,
        "youtube_search_examples": youtube_search_examples,
        "youtube_watch_examples": youtube_watch_examples,
        "ott_examples": ott_examples,
        "job_examples": job_examples,
        "work_examples": work_examples,
        "scheduling_examples": scheduling_examples,
        "admin_examples": admin_examples,
        "project_entries": project_entries,
        "keyword_themes": _top_keywords(
            [item["label"] for item in search_examples]
            + [item["label"] for item in youtube_watch_examples]
            + [item["label"] for item in youtube_search_examples],
            limit=10,
        ),
        "actions": action_items,
        "included_examples": {
            "search_query": search_examples,
            "youtube_search": youtube_search_examples,
            "youtube_watch": youtube_watch_examples,
            "ott_visit": ott_examples,
            "job_or_interview": job_examples,
            "project_or_work_research": work_examples,
            "scheduling_or_comms": scheduling_examples,
            "shopping_or_admin": admin_examples,
        },
        "confidence": confidence,
    }


def _summary_line(kind: str, analysis: dict[str, Any]) -> str:
    bucket_counts = analysis["bucket_counts"]
    return (
        f"{kind.title()} Chrome signals distilled from {analysis['total_visits']} visits; "
        f"kept {analysis['kept_visits']} high-signal events, "
        f"{bucket_counts.get('search_query', 0)} searches, "
        f"{bucket_counts.get('youtube_watch', 0)} YouTube watches, "
        f"{bucket_counts.get('project_or_work_research', 0)} work/project signals."
    )


def _format_examples(items: list[dict[str, Any]]) -> list[str]:
    if not items:
        return ["- none"]
    lines = []
    for item in items:
        suffix = f" ({item['count']}x)"
        if item.get("project_refs"):
            suffix += f" -> {', '.join(item['project_refs'][:2])}"
        lines.append(f"- {item['label']}{suffix}")
    return lines


def build_period_entry(
    *,
    profile: ChromeProfile,
    period_kind: str,
    coverage_start: date,
    coverage_end: date,
    visits: list[ChromeVisit],
    alias_map: dict[str, str],
    signal_kind: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    analysis = analyze_records(visits, alias_map=alias_map)
    label = _period_label(period_kind, coverage_start, coverage_end)
    title = f"Chrome {period_kind} signals: {label}"
    summary = _summary_line(period_kind, analysis)
    body_lines = [
        f"# {title}",
        "",
        f"Profile: {profile.display_name} ({profile.email})",
        f"Coverage: {coverage_start.isoformat()} to {coverage_end.isoformat()} ({settings.digest_timezone})",
        "",
        "## What stood out",
        *[f"- {line}" for line in analysis["actions"]],
        "",
        "## Search themes",
        *_format_examples(analysis["search_examples"]),
        "",
        "## YouTube highlights",
        *_format_examples(analysis["youtube_watch_examples"] or analysis["youtube_search_examples"]),
        "",
        "## OTT and entertainment",
        *_format_examples(analysis["ott_examples"]),
        "",
        "## Work and project signals",
        *_format_examples(analysis["work_examples"]),
        "",
        "## Job / interview signals",
        *_format_examples(analysis["job_examples"]),
        "",
        "## Coordination and admin",
        *_format_examples(analysis["scheduling_examples"] or analysis["admin_examples"]),
        "",
        "## Excluded clutter",
        *_format_key_value_lines(sorted(analysis["excluded_counts"].items())),
    ]
    metadata = {
        "signal_kind": "direct_sync",
        "coverage_start_local": _local_start(coverage_start).isoformat(),
        "coverage_end_local": _local_end(coverage_end).isoformat(),
        "included_examples": analysis["included_examples"],
        "excluded_reasons": analysis["excluded_counts"],
        "profile_email": profile.email,
        "profile_name": profile.display_name,
        "profile_directory": profile.directory,
        "display_timezone": settings.digest_timezone,
        "bucket_counts": analysis["bucket_counts"],
        "keyword_themes": analysis["keyword_themes"],
        "confidence": analysis["confidence"],
        "signal_window": signal_kind,
    }
    entry = {
        "external_id": f"chrome_activity:{signal_kind}:{profile.directory}:{coverage_start.isoformat()}:{coverage_end.isoformat()}",
        "title": title,
        "summary": summary,
        "body_markdown": "\n".join(body_lines).strip(),
        "category": "note",
        "entry_type": "chrome_period_summary" if period_kind in {"weekly", "monthly"} else "chrome_daily_signals",
        "tags": ["chrome-activity", period_kind, "browser-signals"],
        "metadata": metadata,
        "happened_at": _utc_from_local(_local_end(coverage_end)).isoformat(),
        "content_hash": _content_hash({"title": title, "summary": summary, "metadata": metadata}),
    }

    project_entries: list[dict[str, Any]] = []
    min_project_count = 1 if signal_kind == "daily" else 2
    for project_signal in analysis["project_entries"][:4]:
        if project_signal["count"] < min_project_count:
            continue
        project_ref = project_signal["project_ref"]
        project_title = f"Chrome project signal: {project_ref}"
        project_summary = (
            f"{project_ref} surfaced {project_signal['count']} times in Chrome activity during "
            f"{coverage_start.isoformat()} to {coverage_end.isoformat()}."
        )
        project_body = [
            f"# {project_title}",
            "",
            f"Coverage: {coverage_start.isoformat()} to {coverage_end.isoformat()} ({settings.digest_timezone})",
            "",
            "## Examples",
            *_format_examples(project_signal["examples"]),
        ]
        project_entries.append(
            {
                "external_id": (
                    f"chrome_activity:project:{profile.directory}:{project_ref}:{coverage_start.isoformat()}:{coverage_end.isoformat()}"
                ),
                "project_ref": project_ref,
                "title": project_title,
                "summary": project_summary,
                "body_markdown": "\n".join(project_body).strip(),
                "category": "note",
                "entry_type": "chrome_project_signal",
                "tags": ["chrome-activity", "project-signal"],
                "metadata": {
                    **metadata,
                    "project_ref": project_ref,
                    "included_examples": {"project": project_signal["examples"]},
                    "bucket_counts": dict(project_signal["bucket_counts"]),
                },
                "happened_at": entry["happened_at"],
                "content_hash": _content_hash({"title": project_title, "summary": project_summary, "project": project_ref}),
            }
        )

    return entry, project_entries, analysis


def build_profile_entry(
    *,
    profile: ChromeProfile,
    visits: list[ChromeVisit],
    alias_map: dict[str, str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not visits:
        empty_analysis = analyze_records([], alias_map=alias_map)
        entry = {
            "external_id": f"chrome_activity:profile:{profile.directory}",
            "title": f"Chrome profile signals: {profile.display_name}",
            "summary": "No Chrome activity was available to distill.",
            "body_markdown": f"# Chrome profile signals: {profile.display_name}\n\nNo Chrome activity was available to distill.",
            "category": "note",
            "entry_type": "chrome_profile_signal",
            "tags": ["chrome-activity", "profile-summary"],
            "metadata": {
                "signal_kind": "direct_sync",
                "profile_email": profile.email,
                "profile_name": profile.display_name,
                "profile_directory": profile.directory,
                "display_timezone": settings.digest_timezone,
                "included_examples": {},
                "excluded_reasons": empty_analysis["excluded_counts"],
                "bucket_counts": empty_analysis["bucket_counts"],
                "keyword_themes": [],
                "confidence": empty_analysis["confidence"],
            },
            "happened_at": datetime.now(tz=UTC).isoformat(),
            "content_hash": _content_hash({"profile": profile.email, "empty": True}),
        }
        return entry, empty_analysis

    analysis = analyze_records(visits, alias_map=alias_map)
    oldest = visits[0].visited_at_local.date()
    newest = visits[-1].visited_at_local.date()
    body_lines = [
        f"# Chrome profile signals: {profile.display_name}",
        "",
        f"Profile: {profile.display_name} ({profile.email})",
        f"Coverage: {oldest.isoformat()} to {newest.isoformat()} ({settings.digest_timezone})",
        "",
        "## Recurring interests",
        *(
            [f"- {item['term']} ({item['count']} mentions)" for item in analysis["keyword_themes"]]
            or ["- none"]
        ),
        "",
        "## Strongest YouTube patterns",
        *_format_examples(analysis["youtube_watch_examples"]),
        "",
        "## Strongest search patterns",
        *_format_examples(analysis["search_examples"]),
        "",
        "## Projects that kept surfacing",
        *_format_project_lines(analysis["project_entries"][:5]),
        "",
        "## Most actionable observations",
        *[f"- {line}" for line in analysis["actions"]],
    ]
    entry = {
        "external_id": f"chrome_activity:profile:{profile.directory}",
        "title": f"Chrome profile signals: {profile.display_name}",
        "summary": _summary_line("profile", analysis),
        "body_markdown": "\n".join(body_lines).strip(),
        "category": "note",
        "entry_type": "chrome_profile_signal",
        "tags": ["chrome-activity", "profile-summary"],
        "metadata": {
            "signal_kind": "direct_sync",
            "profile_email": profile.email,
            "profile_name": profile.display_name,
            "profile_directory": profile.directory,
            "coverage_start_local": _local_start(oldest).isoformat(),
            "coverage_end_local": _local_end(newest).isoformat(),
            "display_timezone": settings.digest_timezone,
            "included_examples": analysis["included_examples"],
            "excluded_reasons": analysis["excluded_counts"],
            "bucket_counts": analysis["bucket_counts"],
            "keyword_themes": analysis["keyword_themes"],
            "confidence": analysis["confidence"],
        },
        "happened_at": _utc_from_local(_local_end(newest)).isoformat(),
        "content_hash": _content_hash({"profile": profile.email, "analysis": analysis["bucket_counts"], "themes": analysis["keyword_themes"]}),
    }
    return entry, analysis


async def prepare_entries(
    *,
    profile_email: str | None,
    profile_name: str | None,
    mode: str,
    target_date: date | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    profile = resolve_chrome_profile(profile_email=profile_email, profile_name=profile_name)
    alias_map = await load_project_alias_map()

    if mode == "daily":
        target = target_date or (datetime.now(tz=_display_tz()).date() - timedelta(days=1))
        visits = collect_chrome_visits(
            profile,
            start_utc=_utc_from_local(_local_start(target)),
            end_utc=_utc_from_local(_local_start(target + timedelta(days=1))),
        )
        entry, project_entries, analysis = build_period_entry(
            profile=profile,
            period_kind="daily",
            coverage_start=target,
            coverage_end=target,
            visits=visits,
            alias_map=alias_map,
            signal_kind="daily",
        )
        preview = {
            "profile": _profile_payload(profile),
            "mode": mode,
            "coverage": {"start": target.isoformat(), "end": target.isoformat()},
            "total_visits": analysis["total_visits"],
            "kept_visits": analysis["kept_visits"],
            "bucket_counts": analysis["bucket_counts"],
            "excluded_counts": analysis["excluded_counts"],
            "top_included": {
                "searches": analysis["search_examples"],
                "youtube": analysis["youtube_watch_examples"],
                "projects": analysis["project_entries"][:5],
            },
            "planned_entries": [
                {"entry_type": entry["entry_type"], "title": entry["title"], "project_ref": entry.get("project_ref")}
            ]
            + [
                {"entry_type": item["entry_type"], "title": item["title"], "project_ref": item.get("project_ref")}
                for item in project_entries
            ],
        }
        return [entry, *project_entries], preview

    visits = collect_chrome_visits(
        profile,
        end_utc=_utc_from_local(_local_start(datetime.now(tz=_display_tz()).date())),
    )
    if not visits:
        profile_entry, profile_analysis = build_profile_entry(profile=profile, visits=[], alias_map=alias_map)
        preview = {
            "profile": _profile_payload(profile),
            "mode": mode,
            "total_visits": 0,
            "kept_visits": 0,
            "bucket_counts": {},
            "excluded_counts": profile_analysis["excluded_counts"],
            "planned_entries": [{"entry_type": profile_entry["entry_type"], "title": profile_entry["title"]}],
        }
        return [profile_entry], preview

    recent_cutoff = datetime.now(tz=_display_tz()).date() - timedelta(days=settings.chrome_signal_bootstrap_recent_days)
    grouped: dict[tuple[str, date], list[ChromeVisit]] = defaultdict(list)
    for visit in visits:
        local_day = visit.visited_at_local.date()
        key = _period_key_for_visit(local_day, recent_cutoff=recent_cutoff)
        grouped[key].append(visit)

    entries: list[dict[str, Any]] = []
    preview_periods: list[dict[str, Any]] = []
    profile_entry, profile_analysis = build_profile_entry(profile=profile, visits=visits, alias_map=alias_map)
    entries.append(profile_entry)

    for (period_kind, period_start), period_visits in sorted(grouped.items(), key=lambda item: item[0][1]):
        coverage_start, coverage_end = _period_bounds(period_kind, period_start)
        entry, project_entries, analysis = build_period_entry(
            profile=profile,
            period_kind=period_kind,
            coverage_start=coverage_start,
            coverage_end=coverage_end,
            visits=period_visits,
            alias_map=alias_map,
            signal_kind="bootstrap",
        )
        if analysis["kept_visits"] == 0:
            continue
        entries.append(entry)
        entries.extend(project_entries)
        preview_periods.append(
            {
                "entry_type": entry["entry_type"],
                "title": entry["title"],
                "coverage_start": coverage_start.isoformat(),
                "coverage_end": coverage_end.isoformat(),
                "kept_visits": analysis["kept_visits"],
                "bucket_counts": analysis["bucket_counts"],
                "project_refs": [item["project_ref"] for item in analysis["project_entries"][:4]],
            }
        )

    preview = {
        "profile": _profile_payload(profile),
        "mode": mode,
        "coverage": {
            "start": visits[0].visited_at_local.date().isoformat(),
            "end": visits[-1].visited_at_local.date().isoformat(),
        },
        "total_visits": len(visits),
        "kept_visits": profile_analysis["kept_visits"],
        "bucket_counts": profile_analysis["bucket_counts"],
        "excluded_counts": profile_analysis["excluded_counts"],
        "top_included": {
            "searches": profile_analysis["search_examples"],
            "youtube": profile_analysis["youtube_watch_examples"],
            "projects": profile_analysis["project_entries"][:5],
        },
        "planned_entries": (
            [{"entry_type": profile_entry["entry_type"], "title": profile_entry["title"]}] + preview_periods[:50]
        ),
    }
    return entries, preview


async def push_entries(
    *,
    profile_email: str | None,
    profile_name: str | None,
    mode: str,
    target_date: date | None = None,
) -> dict[str, Any]:
    entries, preview = await prepare_entries(
        profile_email=profile_email,
        profile_name=profile_name,
        mode=mode,
        target_date=target_date,
    )
    payload = {
        "source_type": "chrome_activity",
        "source_name": f"mac-chrome-{preview['profile']['directory'].lower().replace(' ', '-')}",
        "mode": mode,
        "device_name": settings.collector_device_name,
    }
    batches = _chunk_entries(entries, CHROME_INGEST_BATCH_SIZE if mode == "bootstrap" else len(entries))
    headers = {"Authorization": f"Bearer {settings.api_token}"} if settings.api_token else {}
    batch_results: list[dict[str, Any]] = []
    async with httpx.AsyncClient(base_url=settings.collector_api_base_url, timeout=180) as client:
        for index, batch in enumerate(batches):
            response = await client.post(
                "/api/ingest/collector",
                headers=headers,
                json={
                    **payload,
                    "mode": mode if len(batches) == 1 else f"{mode}-batch",
                    "emit_sync_event": index == len(batches) - 1,
                    "entries": batch,
                },
            )
            response.raise_for_status()
            batch_results.append(response.json())

    ingest_result = {
        "status": "completed" if batch_results else "noop",
        "items_seen": sum(int(item.get("items_seen", 0)) for item in batch_results),
        "items_imported": sum(int(item.get("items_imported", 0)) for item in batch_results),
        "batches": len(batch_results),
        "batch_sizes": [len(batch) for batch in batches],
    }
    return {"preview": preview, "ingest": ingest_result, "batch_results": batch_results}


def main() -> None:
    parser = argparse.ArgumentParser(description="Distill Chrome Profile 2 history into clean brain signals.")
    parser.add_argument("command", choices=["preview", "ingest"])
    parser.add_argument("--profile-email", default=settings.chrome_signal_profile_email)
    parser.add_argument("--profile-name", default=settings.chrome_signal_profile_name)
    parser.add_argument("--mode", choices=["bootstrap", "daily"], default="daily")
    parser.add_argument("--date", dest="target_date", default=None, help="ISO date for daily ingestion.")
    args = parser.parse_args()

    target_date = date.fromisoformat(args.target_date) if args.target_date else None
    if args.command == "preview":
        _entries, preview = asyncio.run(
            prepare_entries(
                profile_email=args.profile_email,
                profile_name=args.profile_name,
                mode=args.mode,
                target_date=target_date,
            )
        )
        print(json.dumps(preview, indent=2))
        return

    result = asyncio.run(
        push_entries(
            profile_email=args.profile_email,
            profile_name=args.profile_name,
            mode=args.mode,
            target_date=target_date,
        )
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
