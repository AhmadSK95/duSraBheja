from __future__ import annotations

import asyncio
import mailbox
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace

from src.collector import life_exports


def test_collect_gmail_entries_marks_messages_sensitive(tmp_path: Path) -> None:
    mbox_path = tmp_path / "All mail.mbox"
    box = mailbox.mbox(str(mbox_path))
    message = EmailMessage()
    message["Subject"] = "Brain roadmap"
    message["From"] = "ahmad@example.com"
    message["To"] = "me@example.com"
    message["Date"] = "Thu, 12 Mar 2026 13:00:00 +0000"
    message["X-Gmail-Labels"] = "Sent,Important"
    message.set_content("ship the reset script and keep sk-abcdefghijklmnopqrstuvwxyz123456 out of search")
    box.add(message)
    box.flush()
    box.close()

    entries = life_exports.collect_gmail_entries([mbox_path])

    assert len(entries) == 1
    entry = entries[0]
    assert entry["source_type"] == "gmail"
    assert entry["is_sensitive"] is True
    assert "abcdefghijklmnopqrstuvwxyz123456" not in entry["body_markdown"]
    assert entry["metadata"]["gmail_labels"] == ["Sent", "Important"]


def test_collect_keep_entries_reads_json_exports(tmp_path: Path) -> None:
    keep_root = tmp_path / "Keep"
    keep_root.mkdir()
    (keep_root / "note.json").write_text(
        """
        {
          "title": "Weekly priorities",
          "textContent": "Tighten the project identity layer",
          "labels": [{"name": "brain"}],
          "userEditedTimestampUsec": 1773326400000000
        }
        """.strip()
    )

    entries = life_exports.collect_keep_entries([keep_root])

    assert len(entries) == 1
    entry = entries[0]
    assert entry["source_type"] == "google_keep"
    assert entry["title"] == "Weekly priorities"
    assert "project identity layer" in entry["raw_body_markdown"]
    assert "brain" in entry["metadata"]["labels"]


def test_collect_google_activity_entries_parses_takeout_html(tmp_path: Path) -> None:
    history_path = tmp_path / "MyActivity.html"
    history_path.write_text(
        """
        <html><body>
        <div class="outer-cell mdl-cell mdl-cell--12-col mdl-shadow--2dp">
          <div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">
            Watched Building a Second Brain walkthrough
          </div>
          <div class="content-cell mdl-cell mdl-cell--12-col mdl-typography--caption">
            Mar 12, 2026, 9:10:00 AM UTC
          </div>
        </div>
        </body></html>
        """.strip()
    )

    entries = life_exports.collect_google_activity_entries(
        [history_path],
        source_type="youtube_history",
        entry_type="youtube_activity",
        source_tag="youtube-history",
    )

    assert len(entries) == 1
    assert entries[0]["source_type"] == "youtube_history"
    assert entries[0]["title"] == "Watched Building a Second Brain walkthrough"
    assert entries[0]["happened_at"] is not None


def test_collect_ott_entries_reads_csv_rows(tmp_path: Path) -> None:
    ott_root = tmp_path / "netflix"
    ott_root.mkdir()
    (ott_root / "history.csv").write_text(
        "title,watched_at,service\n"
        "Severance,2026-03-12T08:30:00Z,Apple TV+\n"
    )

    entries = life_exports.collect_ott_entries([ott_root])

    assert len(entries) == 1
    assert entries[0]["source_type"] == "ott_history"
    assert entries[0]["title"] == "Severance"
    assert "Apple TV+" in entries[0]["raw_body_markdown"]


def test_prepare_payloads_discovers_takeout_sources(tmp_path: Path) -> None:
    takeout_root = tmp_path / "Takeout"
    (takeout_root / "Mail").mkdir(parents=True)
    (takeout_root / "Drive").mkdir(parents=True)
    (takeout_root / "Keep").mkdir(parents=True)
    (takeout_root / "My Activity" / "YouTube and YouTube Music").mkdir(parents=True)
    (takeout_root / "My Activity" / "Search").mkdir(parents=True)

    mbox_path = takeout_root / "Mail" / "Inbox.mbox"
    box = mailbox.mbox(str(mbox_path))
    message = EmailMessage()
    message["Subject"] = "Context sync"
    message["Date"] = "Thu, 12 Mar 2026 13:00:00 +0000"
    message.set_content("brain session bootstrap")
    box.add(message)
    box.flush()
    box.close()

    (takeout_root / "Drive" / "project.txt").write_text("duSraBheja story notes")
    (takeout_root / "Keep" / "brain.json").write_text('{"title":"Brain note","textContent":"use the brain first"}')
    (takeout_root / "My Activity" / "YouTube and YouTube Music" / "MyActivity.html").write_text(
        '<div class="outer-cell mdl-cell mdl-cell--12-col mdl-shadow--2dp">Watched Story mode</div>'
    )
    (takeout_root / "My Activity" / "Search" / "MyActivity.html").write_text(
        '<div class="outer-cell mdl-cell mdl-cell--12-col mdl-shadow--2dp">Searched for project identity</div>'
    )

    args = SimpleNamespace(
        mode="bootstrap",
        takeout_root=str(takeout_root),
        gmail_mbox=None,
        drive_root=None,
        keep_root=None,
        youtube_history=None,
        google_search_history=None,
        ott_root=None,
    )

    payloads, meta = asyncio.run(life_exports.prepare_payloads(args))

    assert payloads
    assert meta["source_counts"]["gmail"] == 1
    assert meta["source_counts"]["drive"] == 1
    assert meta["source_counts"]["google_keep"] == 1
    assert meta["source_counts"]["youtube_history"] == 1
    assert meta["source_counts"]["google_search_history"] == 1
