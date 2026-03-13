"""Local browser activity collector for end-of-day summaries."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

from src.config import settings

CHROMIUM_SOURCES = {
    "chrome": "~/Library/Application Support/Google/Chrome/Default/History",
    "brave": "~/Library/Application Support/BraveSoftware/Brave-Browser/Default/History",
}
SAFARI_SOURCE = "~/Library/Safari/History.db"
OTT_DOMAINS = {
    "netflix.com",
    "hulu.com",
    "primevideo.com",
    "disneyplus.com",
    "hotstar.com",
    "max.com",
    "peacocktv.com",
    "youtube.com/tv",
}
INTERVIEW_DOMAINS = {
    "leetcode.com",
    "hackerrank.com",
    "codesignal.com",
    "interviewing.io",
    "pramp.com",
    "linkedin.com",
    "indeed.com",
    "glassdoor.com",
}


@dataclass
class BrowserVisit:
    source: str
    visited_at: datetime
    url: str
    title: str


def _expand(path: str) -> Path:
    return Path(os.path.expanduser(path))


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
    return datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=value)


def _safari_to_datetime(value: float) -> datetime:
    return datetime(2001, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=value)


def _read_chromium_history(source_name: str, path: Path, target_date: date) -> list[BrowserVisit]:
    copied = _copy_db(path)
    if not copied:
        return []
    start = datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    start_value = int((start - datetime(1601, 1, 1, tzinfo=timezone.utc)).total_seconds() * 1_000_000)
    end_value = int((end - datetime(1601, 1, 1, tzinfo=timezone.utc)).total_seconds() * 1_000_000)
    visits: list[BrowserVisit] = []
    try:
        conn = sqlite3.connect(copied)
        rows = conn.execute(
            """
            SELECT urls.url, urls.title, visits.visit_time
            FROM visits
            JOIN urls ON visits.url = urls.id
            WHERE visits.visit_time >= ? AND visits.visit_time < ?
            ORDER BY visits.visit_time ASC
            """,
            (start_value, end_value),
        ).fetchall()
        for url, title, visit_time in rows:
            visits.append(
                BrowserVisit(
                    source=source_name,
                    visited_at=_chromium_to_datetime(int(visit_time)),
                    url=url or "",
                    title=title or "",
                )
            )
    finally:
        try:
            conn.close()
        except Exception:
            pass
        copied.unlink(missing_ok=True)
    return visits


def _read_safari_history(path: Path, target_date: date) -> list[BrowserVisit]:
    copied = _copy_db(path)
    if not copied:
        return []
    start = datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    visits: list[BrowserVisit] = []
    try:
        conn = sqlite3.connect(copied)
        rows = conn.execute(
            """
            SELECT history_items.url, history_visits.title, history_visits.visit_time
            FROM history_visits
            JOIN history_items ON history_visits.history_item = history_items.id
            WHERE history_visits.visit_time >= ? AND history_visits.visit_time < ?
            ORDER BY history_visits.visit_time ASC
            """,
            (
                (start - datetime(2001, 1, 1, tzinfo=timezone.utc)).total_seconds(),
                (end - datetime(2001, 1, 1, tzinfo=timezone.utc)).total_seconds(),
            ),
        ).fetchall()
        for url, title, visit_time in rows:
            visits.append(
                BrowserVisit(
                    source="safari",
                    visited_at=_safari_to_datetime(float(visit_time)),
                    url=url or "",
                    title=title or "",
                )
            )
    finally:
        try:
            conn.close()
        except Exception:
            pass
        copied.unlink(missing_ok=True)
    return visits


def collect_browser_visits(target_date: date) -> list[BrowserVisit]:
    visits: list[BrowserVisit] = []
    for name, path in CHROMIUM_SOURCES.items():
        visits.extend(_read_chromium_history(name, _expand(path), target_date))
    visits.extend(_read_safari_history(_expand(SAFARI_SOURCE), target_date))
    return sorted(visits, key=lambda item: item.visited_at)


def _domain(url: str) -> str:
    parsed = urlparse(url)
    return parsed.netloc.lower().removeprefix("www.")


def _search_query(url: str) -> str | None:
    parsed = urlparse(url)
    if "google." in parsed.netloc and parsed.path == "/search":
        return parse_qs(parsed.query).get("q", [None])[0]
    if "bing.com" in parsed.netloc and parsed.path == "/search":
        return parse_qs(parsed.query).get("q", [None])[0]
    if "duckduckgo.com" in parsed.netloc:
        return parse_qs(parsed.query).get("q", [None])[0]
    if "youtube.com" in parsed.netloc and parsed.path == "/results":
        return parse_qs(parsed.query).get("search_query", [None])[0]
    return None


def _youtube_title(visit: BrowserVisit) -> str | None:
    parsed = urlparse(visit.url)
    if "youtube.com" in parsed.netloc and parsed.path == "/watch":
        return visit.title or visit.url
    if "youtu.be" in parsed.netloc:
        return visit.title or visit.url
    return None


def summarize_browser_activity(visits: list[BrowserVisit], *, target_date: date) -> dict:
    searches: list[str] = []
    youtube: list[str] = []
    ott: Counter[str] = Counter()
    interview: Counter[str] = Counter()
    domains: Counter[str] = Counter()
    sources: Counter[str] = Counter()

    for visit in visits:
        domain = _domain(visit.url)
        if not domain:
            continue
        domains[domain] += 1
        sources[visit.source] += 1
        query = _search_query(visit.url)
        if query:
            searches.append(query)
        title = _youtube_title(visit)
        if title:
            youtube.append(title)
        if any(domain.endswith(candidate) for candidate in OTT_DOMAINS):
            ott[domain] += 1
        if any(domain.endswith(candidate) for candidate in INTERVIEW_DOMAINS):
            interview[domain] += 1

    sections = defaultdict(list)
    for item in searches[:8]:
        sections["searches"].append(f"- {item}")
    for item in youtube[:8]:
        sections["youtube"].append(f"- {item}")
    for domain, count in ott.most_common(5):
        sections["ott"].append(f"- {domain} ({count} visits)")
    for domain, count in interview.most_common(5):
        sections["interview"].append(f"- {domain} ({count} visits)")
    for domain, count in domains.most_common(10):
        sections["top_domains"].append(f"- {domain} ({count} visits)")

    lines = [
        f"# Browser activity for {target_date.isoformat()}",
        "",
        f"Sources seen: {', '.join(f'{name}:{count}' for name, count in sources.items()) or 'none'}",
        "",
        "## Search queries",
        *(sections["searches"] or ["- None captured"]),
        "",
        "## YouTube activity",
        *(sections["youtube"] or ["- None captured"]),
        "",
        "## Interview / service browsing",
        *(sections["interview"] or ["- None captured"]),
        "",
        "## OTT / leisure domains",
        *(sections["ott"] or ["- None captured"]),
        "",
        "## Top domains",
        *(sections["top_domains"] or ["- None captured"]),
    ]
    body = "\n".join(lines)
    return {
        "title": f"Browser activity for {target_date.isoformat()}",
        "summary": (
            f"{len(visits)} local browser visits captured. "
            f"{len(searches)} searches, {len(youtube)} YouTube items, {sum(ott.values())} OTT visits."
        ),
        "body_markdown": body,
        "metadata": {
            "sources": dict(sources),
            "top_domains": domains.most_common(10),
            "searches": searches[:20],
            "youtube_titles": youtube[:20],
            "ott_domains": ott.most_common(10),
            "interview_domains": interview.most_common(10),
        },
    }


async def push_browser_activity_summary(target_date: date) -> dict:
    visits = collect_browser_visits(target_date)
    summary = summarize_browser_activity(visits, target_date=target_date)
    payload = {
        "source_type": "browser_activity",
        "source_name": "mac-browser-activity",
        "mode": "sync",
        "device_name": settings.collector_device_name,
        "emit_sync_event": True,
        "entries": [
            {
                "external_id": f"browser-activity:{target_date.isoformat()}",
                "title": summary["title"],
                "summary": summary["summary"],
                "body_markdown": summary["body_markdown"],
                "category": "note",
                "entry_type": "browser_activity",
                "tags": ["browser-activity", "daily-observability"],
                "metadata": summary["metadata"],
                "happened_at": datetime.combine(target_date, datetime.max.time(), tzinfo=timezone.utc).isoformat(),
            }
        ],
    }
    headers = {"Authorization": f"Bearer {settings.api_token}"} if settings.api_token else {}
    async with httpx.AsyncClient(base_url=settings.collector_api_base_url, timeout=60) as client:
        response = await client.post("/api/ingest/collector", headers=headers, json=payload)
        response.raise_for_status()
        return response.json()


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect local browser activity and push a daily summary to the brain.")
    parser.add_argument("mode", choices=["sync", "print"], nargs="?", default="print")
    parser.add_argument("--date", dest="target_date", default=None, help="ISO date to collect, defaults to yesterday")
    args = parser.parse_args()
    target_date = date.fromisoformat(args.target_date) if args.target_date else (datetime.now().date() - timedelta(days=1))
    if args.mode == "print":
        visits = collect_browser_visits(target_date)
        print(json.dumps(summarize_browser_activity(visits, target_date=target_date), indent=2))
        return

    import asyncio

    result = asyncio.run(push_browser_activity_summary(target_date))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
