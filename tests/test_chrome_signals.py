from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import pytest

from src.collector import chrome_signals


def _make_visit(*, url: str, title: str, dt_utc: datetime) -> chrome_signals.ChromeVisit:
    return chrome_signals.ChromeVisit(
        url=url,
        title=title,
        domain=chrome_signals._normalize_hostname(urlparse(url).netloc),
        visited_at_utc=dt_utc,
        visited_at_local=dt_utc.astimezone(chrome_signals._display_tz()),
    )


def test_resolve_chrome_profile_prefers_profile_email(tmp_path: Path) -> None:
    chrome_root = tmp_path / "Chrome"
    local_state = chrome_root / "Local State"
    profile_dir = chrome_root / "Profile 2"
    profile_dir.mkdir(parents=True)
    (profile_dir / "History").write_bytes(b"db")
    local_state.write_text(
        json.dumps(
            {
                "profile": {
                    "info_cache": {
                        "Default": {"name": "Ahmad", "user_name": "other@example.com", "gaia_name": "Other"},
                        "Profile 2": {
                            "name": "Ahmad",
                            "user_name": "ahmad2609.as@gmail.com",
                            "gaia_name": "Ahmad Shaik",
                        },
                    }
                }
            }
        )
    )

    profile = chrome_signals.resolve_chrome_profile(
        profile_email="ahmad2609.as@gmail.com",
        local_state_path=local_state,
        chrome_root=chrome_root,
    )

    assert profile.directory == "Profile 2"
    assert profile.email == "ahmad2609.as@gmail.com"


def test_classify_visit_excludes_sensitive_domains() -> None:
    visit = _make_visit(
        url="https://mail.google.com/mail/u/0/#inbox",
        title="Inbox",
        dt_utc=datetime.now(tz=UTC),
    )

    record, reason = chrome_signals.classify_visit(
        visit,
        alias_map={},
        excluded_domains=chrome_signals.DEFAULT_EXCLUDED_DOMAINS,
        excluded_patterns={pattern.lower() for pattern in chrome_signals.DEFAULT_EXCLUDED_URL_PATTERNS},
    )

    assert record is None
    assert reason == "sensitive_or_excluded_domain"


@pytest.mark.asyncio
async def test_prepare_entries_bootstrap_creates_period_summaries_and_project_signal(monkeypatch) -> None:
    today_local = datetime.now(tz=chrome_signals._display_tz()).date()
    recent_day = today_local - timedelta(days=7)
    older_day = today_local - timedelta(days=140)

    profile = chrome_signals.ChromeProfile(
        directory="Profile 2",
        display_name="Ahmad",
        email="ahmad2609.as@gmail.com",
        gaia_name="Ahmad Shaik",
        history_path=Path("/tmp/History"),
    )

    visits = [
        _make_visit(
            url="https://www.google.com/search?q=duSraBheja+board+first",
            title="duSraBheja board first - Google Search",
            dt_utc=chrome_signals._utc_from_local(chrome_signals._local_start(recent_day) + timedelta(hours=10)),
        ),
        _make_visit(
            url="https://www.google.com/search?q=duSraBheja+retrieval+status",
            title="duSraBheja retrieval status - Google Search",
            dt_utc=chrome_signals._utc_from_local(chrome_signals._local_start(recent_day) + timedelta(hours=11)),
        ),
        _make_visit(
            url="https://youtube.com/watch?v=1",
            title="I Built 6 Things on One Database. Now My AI Runs My House - YouTube",
            dt_utc=chrome_signals._utc_from_local(chrome_signals._local_start(older_day) + timedelta(hours=20)),
        ),
    ]

    monkeypatch.setattr(chrome_signals, "resolve_chrome_profile", lambda **_: profile)
    monkeypatch.setattr(chrome_signals, "collect_chrome_visits", lambda *args, **kwargs: visits)
    async def fake_alias_map() -> dict[str, str]:
        return {"dusrabheja": "duSraBheja - Project Overview"}

    monkeypatch.setattr(chrome_signals, "load_project_alias_map", fake_alias_map)

    entries, preview = await chrome_signals.prepare_entries(
        profile_email="ahmad2609.as@gmail.com",
        profile_name="Ahmad",
        mode="bootstrap",
    )

    entry_types = [entry["entry_type"] for entry in entries]
    assert "chrome_profile_signal" in entry_types
    assert "chrome_period_summary" in entry_types
    assert "chrome_project_signal" in entry_types
    assert preview["profile"]["directory"] == "Profile 2"
    assert preview["top_included"]["projects"]


@pytest.mark.asyncio
async def test_prepare_entries_daily_generates_one_daily_entry(monkeypatch) -> None:
    target_day = datetime.now(tz=chrome_signals._display_tz()).date() - timedelta(days=1)
    profile = chrome_signals.ChromeProfile(
        directory="Profile 2",
        display_name="Ahmad",
        email="ahmad2609.as@gmail.com",
        gaia_name="Ahmad Shaik",
        history_path=Path("/tmp/History"),
    )
    visits = [
        _make_visit(
            url="https://www.google.com/search?q=kaffa+espresso+bar",
            title="kaffa espresso bar - Google Search",
            dt_utc=chrome_signals._utc_from_local(chrome_signals._local_start(target_day) + timedelta(hours=9)),
        ),
        _make_visit(
            url="https://youtube.com/watch?v=2",
            title="AI Made Every Company 10x More Productive - YouTube",
            dt_utc=chrome_signals._utc_from_local(chrome_signals._local_start(target_day) + timedelta(hours=11)),
        ),
    ]

    monkeypatch.setattr(chrome_signals, "resolve_chrome_profile", lambda **_: profile)
    monkeypatch.setattr(chrome_signals, "collect_chrome_visits", lambda *args, **kwargs: visits)
    async def empty_alias_map() -> dict[str, str]:
        return {}

    monkeypatch.setattr(chrome_signals, "load_project_alias_map", empty_alias_map)

    entries, preview = await chrome_signals.prepare_entries(
        profile_email="ahmad2609.as@gmail.com",
        profile_name="Ahmad",
        mode="daily",
        target_date=target_day,
    )

    assert entries[0]["entry_type"] == "chrome_daily_signals"
    assert preview["coverage"]["start"] == target_day.isoformat()
    assert preview["planned_entries"][0]["entry_type"] == "chrome_daily_signals"


def test_low_signal_general_browsing_is_dropped() -> None:
    base = datetime(2026, 3, 14, 12, tzinfo=UTC)
    visits = [
        _make_visit(
            url="https://example.com/article-one",
            title="Interesting Article One",
            dt_utc=base,
        ),
        _make_visit(
            url="https://example.com/article-two",
            title="Interesting Article Two",
            dt_utc=base + timedelta(minutes=10),
        ),
        _make_visit(
            url="https://news.ycombinator.com/item?id=1",
            title="Show HN: Something neat",
            dt_utc=base + timedelta(minutes=20),
        ),
    ]

    analysis = chrome_signals.analyze_records(visits, alias_map={})

    assert analysis["kept_visits"] == 0
    assert analysis["bucket_counts"] == {}
    assert analysis["excluded_counts"]["low_signal_general_browsing"] == 3
