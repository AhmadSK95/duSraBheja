"""One-time import pipeline for personal exports such as Gmail, Drive, Keep, history, and OTT logs."""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import mailbox
import mimetypes
import re
from datetime import UTC, datetime
from email.message import Message
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx

from src.collector.agent_history import MAX_ENTRIES_PER_REQUEST, MAX_REQUEST_BYTES, redact_text
from src.collector.main import stable_id
from src.config import settings
from src.worker.extractors.docx import extract_docx
from src.worker.extractors.excel import extract_excel
from src.worker.extractors.pdf import extract_pdf
from src.worker.extractors.text import extract_text

TEXT_EXPORT_SUFFIXES = {
    ".csv",
    ".html",
    ".json",
    ".jsonl",
    ".md",
    ".rst",
    ".text",
    ".txt",
    ".tsv",
    ".yaml",
    ".yml",
}
DRIVE_SUPPORTED_SUFFIXES = TEXT_EXPORT_SUFFIXES | {".docx", ".pdf", ".xlsx"}
KEEP_LABEL_KEY_CANDIDATES = ("labels", "labels_json", "labelNames")
GOOGLE_ACTIVITY_SPLIT_RE = re.compile(r'<div class="outer-cell[^"]*">', re.IGNORECASE)
TAG_STRIP_RE = re.compile(r"<[^>]+>")
OTT_SERVICE_HINTS = ("netflix", "prime", "hotstar", "disney", "hulu", "max", "peacock", "apple-tv")
MAX_SAFE_BODY_CHARS = 6_000
MAX_RAW_BODY_CHARS = 40_000


def _html_to_text(value: str) -> str:
    text = value or ""
    replacements = {
        "<br>": "\n",
        "<br/>": "\n",
        "<br />": "\n",
        "</div>": "\n",
        "</p>": "\n\n",
        "</li>": "\n",
        "</h1>": "\n\n",
        "</h2>": "\n\n",
        "</h3>": "\n\n",
        "</tr>": "\n",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
        text = text.replace(source.upper(), target)
    text = re.sub(r"<li[^>]*>", "- ", text, flags=re.IGNORECASE)
    text = TAG_STRIP_RE.sub("", text)
    text = re.sub(r"&nbsp;", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"&amp;", "&", text, flags=re.IGNORECASE)
    text = re.sub(r"\r", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _safe_excerpt(text: str) -> str:
    return redact_text((text or "").strip()[:MAX_SAFE_BODY_CHARS])


def _raw_excerpt(text: str) -> str:
    return (text or "").strip()[:MAX_RAW_BODY_CHARS]


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _iso(value: datetime | None) -> str | None:
    if not value:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _coerce_datetime(value: object) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, (int, float)):
        number = int(value)
        if number > 10_000_000_000_000:
            return _iso(datetime.fromtimestamp(number / 1_000_000, tz=UTC))
        if number > 10_000_000_000:
            return _iso(datetime.fromtimestamp(number / 1_000, tz=UTC))
        return _iso(datetime.fromtimestamp(number, tz=UTC))
    text = str(value).strip()
    if not text:
        return None
    try:
        return _iso(parsedate_to_datetime(text))
    except (TypeError, ValueError, IndexError, OverflowError):
        pass
    cleaned = text.replace("Z", "+00:00")
    for fmt in (
        "%b %d, %Y, %I:%M:%S %p %Z",
        "%b %d, %Y, %I:%M:%S %p",
        "%b %d, %Y, %I:%M %p",
        "%B %d, %Y, %I:%M:%S %p %Z",
        "%B %d, %Y, %I:%M:%S %p",
        "%B %d, %Y, %I:%M %p",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            parsed = datetime.strptime(cleaned, fmt)
            return _iso(parsed)
        except ValueError:
            continue
    return None


def _slug_candidates(*values: str | None) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        candidates.append(cleaned)
    return candidates


def discover_takeout_paths(takeout_root: Path) -> dict[str, list[Path]]:
    root = takeout_root.expanduser().resolve()
    if not root.exists():
        return {
            "gmail_mboxes": [],
            "drive_roots": [],
            "keep_roots": [],
            "youtube_paths": [],
            "search_paths": [],
        }

    gmail_mboxes = sorted(path for path in root.rglob("*.mbox") if path.is_file())

    drive_roots = sorted(
        {
            path
            for path in root.rglob("*")
            if path.is_dir() and path.name.lower() in {"drive", "my drive"}
        }
    )
    keep_roots = sorted(
        {
            path
            for path in root.rglob("*")
            if path.is_dir() and path.name.lower() in {"keep", "google keep"}
        }
    )

    youtube_paths: set[Path] = set()
    search_paths: set[Path] = set()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        lowered = str(path).lower()
        if path.name.lower() in {"myactivity.html", "watch-history.html", "search-history.html"}:
            if "youtube" in lowered:
                youtube_paths.add(path)
            if re.search(r"(^|[/\\])search([/\\]|$)", lowered):
                search_paths.add(path)

    return {
        "gmail_mboxes": gmail_mboxes,
        "drive_roots": drive_roots,
        "keep_roots": keep_roots,
        "youtube_paths": sorted(youtube_paths),
        "search_paths": sorted(search_paths),
    }


def _message_body_text(message: Message) -> str:
    if message.is_multipart():
        parts: list[str] = []
        html_parts: list[str] = []
        for part in message.walk():
            if part.get_content_maintype() == "multipart":
                continue
            disposition = (part.get("Content-Disposition") or "").lower()
            if "attachment" in disposition:
                continue
            payload = part.get_payload(decode=True) or b""
            charset = part.get_content_charset() or "utf-8"
            decoded = payload.decode(charset, errors="replace")
            if part.get_content_type() == "text/plain" and decoded.strip():
                parts.append(decoded.strip())
            elif part.get_content_type() == "text/html" and decoded.strip():
                html_parts.append(_html_to_text(decoded))
        if parts:
            return "\n\n".join(parts).strip()
        if html_parts:
            return "\n\n".join(html_parts).strip()

    payload = message.get_payload(decode=True)
    if isinstance(payload, bytes):
        charset = message.get_content_charset() or "utf-8"
        decoded = payload.decode(charset, errors="replace")
    else:
        decoded = str(payload or "")
    if message.get_content_type() == "text/html":
        return _html_to_text(decoded)
    return decoded.strip()


def collect_gmail_entries(mbox_paths: list[Path]) -> list[dict]:
    entries: list[dict] = []
    for mbox_path in mbox_paths:
        if not mbox_path.exists():
            continue
        mailbox_reader = mailbox.mbox(str(mbox_path))
        for key, message in mailbox_reader.items():
            subject = (message.get("Subject") or "").strip() or f"Email in {mbox_path.stem}"
            sender = (message.get("From") or "").strip()
            recipients = (message.get("To") or "").strip()
            labels = [item.strip() for item in (message.get("X-Gmail-Labels") or "").split(",") if item.strip()]
            body_text = _message_body_text(message)
            body_markdown = "\n".join(
                line
                for line in [
                    f"# {subject}",
                    f"From: {sender}" if sender else "",
                    f"To: {recipients}" if recipients else "",
                    f"Date: {message.get('Date') or ''}".strip(),
                    f"Labels: {', '.join(labels)}" if labels else "",
                    "",
                    body_text or "[empty message]",
                ]
                if line is not None
            ).strip()
            happened_at = _coerce_datetime(message.get("Date"))
            raw_body = _raw_excerpt(body_markdown)
            safe_body = _safe_excerpt(body_markdown)
            message_id = (message.get("Message-ID") or "").strip("<> ")
            external_key = message_id or f"{mbox_path}:{key}"
            entries.append(
                {
                    "source_type": "gmail",
                    "external_id": f"gmail:message:{stable_id(external_key)}",
                    "title": subject[:240],
                    "summary": safe_body[:240],
                    "category": "note",
                    "entry_type": "email_message",
                    "body_markdown": safe_body,
                    "raw_body_markdown": raw_body,
                    "tags": ["gmail", *[candidate for candidate in _slug_candidates(*labels)[:6]]],
                    "source_links": [],
                    "external_url": None,
                    "happened_at": happened_at,
                    "is_sensitive": True,
                    "metadata": {
                        "sensitive": True,
                        "from": sender,
                        "to": recipients,
                        "mailbox_path": str(mbox_path),
                        "gmail_labels": labels,
                        "message_id": message_id,
                    },
                    "content_hash": _content_hash(raw_body),
                }
            )
    return entries


async def _extract_drive_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return await extract_pdf(str(path))
    if suffix == ".docx":
        return await extract_docx(str(path))
    if suffix == ".xlsx":
        return await extract_excel(str(path))
    if suffix in DRIVE_SUPPORTED_SUFFIXES:
        return await extract_text(str(path))
    return "[binary or unsupported file]"


async def collect_drive_entries(drive_roots: list[Path]) -> list[dict]:
    entries: list[dict] = []
    for drive_root in drive_roots:
        if not drive_root.exists():
            continue
        for path in sorted(drive_root.rglob("*")):
            if not path.is_file():
                continue
            if path.name.startswith("."):
                continue
            if path.suffix.lower() not in DRIVE_SUPPORTED_SUFFIXES:
                mime_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
                extracted_text = f"# {path.name}\n\nPath: {path}\nMIME: {mime_type}\n\n[binary or unsupported file]"
            else:
                extracted_text = await _extract_drive_text(path)
            relative_path = str(path.relative_to(drive_root))
            raw_body = _raw_excerpt(f"# {path.name}\n\nPath: {relative_path}\n\n{extracted_text}")
            safe_body = _safe_excerpt(raw_body)
            entries.append(
                {
                    "source_type": "drive",
                    "external_id": f"drive:file:{stable_id(str(path))}",
                    "title": path.name[:240],
                    "summary": safe_body[:240],
                    "category": "resource",
                    "entry_type": "drive_file",
                    "body_markdown": safe_body,
                    "raw_body_markdown": raw_body,
                    "tags": ["drive", *[candidate for candidate in _slug_candidates(path.parent.name)[:4]]],
                    "source_links": [],
                    "external_url": None,
                    "happened_at": _coerce_datetime(datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)),
                    "is_sensitive": True,
                    "metadata": {
                        "sensitive": True,
                        "relative_path": relative_path,
                        "drive_root": str(drive_root),
                        "size_bytes": path.stat().st_size,
                    },
                    "content_hash": _content_hash(raw_body),
                }
            )
    return entries


def _keep_note_body(note: dict, *, fallback_title: str) -> tuple[str, list[str], str | None]:
    title = str(note.get("title") or fallback_title or "Untitled Keep note").strip()
    labels = []
    for key in KEEP_LABEL_KEY_CANDIDATES:
        value = note.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    label_name = item.get("name")
                else:
                    label_name = item
                if label_name:
                    labels.append(str(label_name))
    text_blocks: list[str] = []
    if note.get("textContent"):
        text_blocks.append(str(note["textContent"]))
    list_content = note.get("listContent") or []
    for item in list_content:
        if not isinstance(item, dict):
            continue
        prefix = "- [x]" if item.get("isChecked") else "- [ ]"
        text_blocks.append(f"{prefix} {item.get('text') or ''}".strip())
    attachments = note.get("attachments") or []
    if attachments:
        text_blocks.append(
            "Attachments: " + ", ".join(str(item.get("filePath") or item.get("mimetype") or "attachment") for item in attachments)
        )
    body_markdown = "\n".join(
        line for line in [f"# {title}", f"Labels: {', '.join(labels)}" if labels else "", "", *text_blocks] if line is not None
    ).strip()
    happened_at = _coerce_datetime(
        note.get("userEditedTimestampUsec")
        or note.get("userEditedTimestampMs")
        or note.get("createdTimestampUsec")
        or note.get("createdTimestampMs")
    )
    return body_markdown or f"# {title}\n\n[empty note]", labels, happened_at


def collect_keep_entries(keep_roots: list[Path]) -> list[dict]:
    entries: list[dict] = []
    for keep_root in keep_roots:
        if not keep_root.exists():
            continue
        seen_stems: set[Path] = set()
        for json_path in sorted(keep_root.rglob("*.json")):
            if json_path.name == "manifest.json":
                continue
            note = json.loads(json_path.read_text(encoding="utf-8", errors="replace"))
            body_markdown, labels, happened_at = _keep_note_body(note, fallback_title=json_path.stem)
            safe_body = _safe_excerpt(body_markdown)
            raw_body = _raw_excerpt(body_markdown)
            note_id = note.get("id") or note.get("serverId") or json_path.stem
            entries.append(
                {
                    "source_type": "google_keep",
                    "external_id": f"google_keep:note:{stable_id(str(note_id))}",
                    "title": str(note.get("title") or json_path.stem)[:240],
                    "summary": safe_body[:240],
                    "category": "note",
                    "entry_type": "keep_note",
                    "body_markdown": safe_body,
                    "raw_body_markdown": raw_body,
                    "tags": ["google-keep", *[candidate for candidate in _slug_candidates(*labels)[:6]]],
                    "source_links": [],
                    "external_url": None,
                    "happened_at": happened_at,
                    "is_sensitive": True,
                    "metadata": {
                        "sensitive": True,
                        "keep_root": str(keep_root),
                        "note_json_path": str(json_path),
                        "labels": labels,
                    },
                    "content_hash": _content_hash(raw_body),
                }
            )
            seen_stems.add(json_path.with_suffix(""))

        for html_path in sorted(keep_root.rglob("*.html")):
            if html_path.with_suffix("") in seen_stems:
                continue
            body_markdown = f"# {html_path.stem}\n\n{_html_to_text(html_path.read_text(encoding='utf-8', errors='replace'))}"
            safe_body = _safe_excerpt(body_markdown)
            raw_body = _raw_excerpt(body_markdown)
            entries.append(
                {
                    "source_type": "google_keep",
                    "external_id": f"google_keep:note:{stable_id(str(html_path))}",
                    "title": html_path.stem[:240],
                    "summary": safe_body[:240],
                    "category": "note",
                    "entry_type": "keep_note",
                    "body_markdown": safe_body,
                    "raw_body_markdown": raw_body,
                    "tags": ["google-keep"],
                    "source_links": [],
                    "external_url": None,
                    "happened_at": _coerce_datetime(datetime.fromtimestamp(html_path.stat().st_mtime, tz=UTC)),
                    "is_sensitive": True,
                    "metadata": {
                        "sensitive": True,
                        "keep_root": str(keep_root),
                        "note_html_path": str(html_path),
                    },
                    "content_hash": _content_hash(raw_body),
                }
            )
    return entries


def _activity_lines(block: str) -> list[str]:
    text = _html_to_text(block)
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return [line for line in lines if line and line.lower() not in {"products"}]


def _activity_title(lines: list[str], *, fallback: str) -> str:
    ignored_prefixes = ("products:", "details", "google products")
    for line in lines:
        lowered = line.lower()
        if lowered.startswith(ignored_prefixes):
            continue
        if len(line) < 3:
            continue
        return line[:240]
    return fallback


def _activity_summary(lines: list[str]) -> str:
    usable = [line for line in lines if not line.lower().startswith("products:")]
    return " | ".join(usable[:5])


def collect_google_activity_entries(paths: list[Path], *, source_type: str, entry_type: str, source_tag: str) -> list[dict]:
    entries: list[dict] = []
    for path in paths:
        if not path.exists():
            continue
        blocks = GOOGLE_ACTIVITY_SPLIT_RE.split(path.read_text(encoding="utf-8", errors="replace"))
        for index, block in enumerate(blocks[1:], 1):
            lines = _activity_lines(block)
            if not lines:
                continue
            title = _activity_title(lines, fallback=f"{source_tag} activity {index}")
            summary = _activity_summary(lines)
            happened_at = next((_coerce_datetime(line) for line in reversed(lines) if _coerce_datetime(line)), None)
            raw_body = _raw_excerpt("\n".join([f"# {title}", "", *lines]))
            safe_body = _safe_excerpt(raw_body)
            entries.append(
                {
                    "source_type": source_type,
                    "external_id": f"{source_type}:activity:{stable_id(f'{path}:{index}:{title}:{happened_at}')}",
                    "title": title,
                    "summary": safe_body[:240],
                    "category": "note",
                    "entry_type": entry_type,
                    "body_markdown": safe_body,
                    "raw_body_markdown": raw_body,
                    "tags": [source_tag],
                    "source_links": [],
                    "external_url": None,
                    "happened_at": happened_at,
                    "is_sensitive": True,
                    "metadata": {
                        "sensitive": True,
                        "export_path": str(path),
                    },
                    "content_hash": _content_hash(raw_body),
                }
            )
    return entries


def _iter_ott_rows(path: Path) -> list[dict]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    if suffix == ".jsonl":
        rows = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return [row for row in rows if isinstance(row, dict)]
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        if isinstance(payload, dict):
            for key in ("items", "history", "watchHistory", "events", "rows"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [row for row in value if isinstance(row, dict)]
            return [payload]
    if suffix in {".html", ".txt"}:
        text = _html_to_text(path.read_text(encoding="utf-8", errors="replace"))
        return [{"title": line} for line in text.splitlines() if line.strip()]
    return []


def _ott_service_name(path: Path, row: dict) -> str:
    for key in ("service", "platform", "provider", "app"):
        value = row.get(key)
        if value:
            return str(value)
    lowered_parts = [part.lower() for part in path.parts]
    for hint in OTT_SERVICE_HINTS:
        if hint in lowered_parts or any(hint in part for part in lowered_parts):
            return hint
    return path.stem


def _ott_title(row: dict, *, fallback: str) -> str:
    for key in ("title", "name", "video_title", "content_title", "program", "series_title"):
        value = row.get(key)
        if value:
            return str(value)
    return fallback


def _ott_happened_at(row: dict) -> str | None:
    for key in (
        "watched_at",
        "viewed_at",
        "played_at",
        "date",
        "time",
        "last_watched",
        "updated_at",
        "created_at",
    ):
        value = row.get(key)
        if value:
            converted = _coerce_datetime(value)
            if converted:
                return converted
    return None


def collect_ott_entries(ott_roots: list[Path]) -> list[dict]:
    entries: list[dict] = []
    for ott_root in ott_roots:
        if not ott_root.exists():
            continue
        for path in sorted(ott_root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in {".csv", ".json", ".jsonl", ".html", ".txt"}:
                continue
            rows = _iter_ott_rows(path)
            if not rows:
                continue
            for index, row in enumerate(rows, 1):
                service = _ott_service_name(path, row)
                title = _ott_title(row, fallback=f"{service} watch event {index}")
                happened_at = _ott_happened_at(row) or _coerce_datetime(datetime.fromtimestamp(path.stat().st_mtime, tz=UTC))
                raw_body = _raw_excerpt(
                    "\n".join(
                        [
                            f"# {title}",
                            f"Service: {service}",
                            f"Source file: {path}",
                            "",
                            json.dumps(row, indent=2, sort_keys=True) if isinstance(row, dict) else str(row),
                        ]
                    )
                )
                safe_body = _safe_excerpt(raw_body)
                entries.append(
                    {
                        "source_type": "ott_history",
                        "external_id": f"ott_history:item:{stable_id(f'{path}:{index}:{title}:{happened_at}')}",
                        "title": title[:240],
                        "summary": safe_body[:240],
                        "category": "note",
                        "entry_type": "ott_watch_event",
                        "body_markdown": safe_body,
                        "raw_body_markdown": raw_body,
                        "tags": ["ott-history", *[candidate for candidate in _slug_candidates(service)[:3]]],
                        "source_links": [],
                        "external_url": None,
                        "happened_at": happened_at,
                        "is_sensitive": True,
                        "metadata": {
                            "sensitive": True,
                            "service": service,
                            "source_file": str(path),
                        },
                        "content_hash": _content_hash(raw_body),
                    }
                )
    return entries


def batch_payloads(entries: list[dict], *, mode: str) -> list[dict]:
    by_source: dict[str, list[dict]] = {}
    for entry in entries:
        by_source.setdefault(entry["source_type"], []).append(entry)

    payloads: list[dict] = []
    for source_type, source_entries in sorted(by_source.items()):
        source_name = f"mac-{source_type.replace('_', '-')}-backfill"
        batch: list[dict] = []
        batch_bytes = 0
        for entry in source_entries:
            entry_bytes = len(json.dumps(entry).encode("utf-8"))
            if batch and (len(batch) >= MAX_ENTRIES_PER_REQUEST or batch_bytes + entry_bytes > MAX_REQUEST_BYTES):
                payloads.append(
                    {
                        "source_type": source_type,
                        "source_name": source_name,
                        "mode": mode,
                        "device_name": settings.collector_device_name,
                        "entries": [{key: value for key, value in item.items() if key != "source_type"} for item in batch],
                    }
                )
                batch = []
                batch_bytes = 0
            batch.append(entry)
            batch_bytes += entry_bytes
        if batch:
            payloads.append(
                {
                    "source_type": source_type,
                    "source_name": source_name,
                    "mode": mode,
                    "device_name": settings.collector_device_name,
                    "entries": [{key: value for key, value in item.items() if key != "source_type"} for item in batch],
                }
            )
    return payloads


async def prepare_payloads(args: argparse.Namespace) -> tuple[list[dict], dict]:
    takeout_paths = discover_takeout_paths(Path(args.takeout_root).expanduser()) if args.takeout_root else {}
    gmail_mboxes = sorted({Path(path).expanduser().resolve() for path in (args.gmail_mbox or [])} | set(takeout_paths.get("gmail_mboxes", [])))
    drive_roots = sorted({Path(path).expanduser().resolve() for path in (args.drive_root or [])} | set(takeout_paths.get("drive_roots", [])))
    keep_roots = sorted({Path(path).expanduser().resolve() for path in (args.keep_root or [])} | set(takeout_paths.get("keep_roots", [])))
    youtube_paths = sorted({Path(path).expanduser().resolve() for path in (args.youtube_history or [])} | set(takeout_paths.get("youtube_paths", [])))
    search_paths = sorted({Path(path).expanduser().resolve() for path in (args.google_search_history or [])} | set(takeout_paths.get("search_paths", [])))
    ott_roots = sorted({Path(path).expanduser().resolve() for path in (args.ott_root or [])})

    gmail_entries = collect_gmail_entries(gmail_mboxes)
    drive_entries = await collect_drive_entries(drive_roots)
    keep_entries = collect_keep_entries(keep_roots)
    youtube_entries = collect_google_activity_entries(
        youtube_paths,
        source_type="youtube_history",
        entry_type="youtube_activity",
        source_tag="youtube-history",
    )
    search_entries = collect_google_activity_entries(
        search_paths,
        source_type="google_search_history",
        entry_type="google_search_activity",
        source_tag="google-search-history",
    )
    ott_entries = collect_ott_entries(ott_roots)

    entries = [
        *gmail_entries,
        *drive_entries,
        *keep_entries,
        *youtube_entries,
        *search_entries,
        *ott_entries,
    ]
    entries.sort(key=lambda item: item.get("happened_at") or "", reverse=False)
    payloads = batch_payloads(entries, mode=args.mode)
    meta = {
        "mode": args.mode,
        "items_seen": len(entries),
        "request_count": len(payloads),
        "source_counts": {
            "gmail": len(gmail_entries),
            "drive": len(drive_entries),
            "google_keep": len(keep_entries),
            "youtube_history": len(youtube_entries),
            "google_search_history": len(search_entries),
            "ott_history": len(ott_entries),
        },
        "paths": {
            "gmail_mbox": [str(path) for path in gmail_mboxes],
            "drive_root": [str(path) for path in drive_roots],
            "keep_root": [str(path) for path in keep_roots],
            "youtube_history": [str(path) for path in youtube_paths],
            "google_search_history": [str(path) for path in search_paths],
            "ott_root": [str(path) for path in ott_roots],
        },
    }
    return payloads, meta


async def post_payloads(payloads: list[dict]) -> list[dict]:
    headers = {"Authorization": f"Bearer {settings.api_token}"}
    results: list[dict] = []
    async with httpx.AsyncClient(base_url=settings.collector_api_base_url, timeout=120) as client:
        for payload in payloads:
            response = await client.post("/api/ingest/collector", headers=headers, json=payload)
            response.raise_for_status()
            results.append(response.json())
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="One-time personal export importer for the brain")
    parser.add_argument("mode", choices=["bootstrap", "sync"])
    parser.add_argument("--takeout-root")
    parser.add_argument("--gmail-mbox", action="append")
    parser.add_argument("--drive-root", action="append")
    parser.add_argument("--keep-root", action="append")
    parser.add_argument("--youtube-history", action="append")
    parser.add_argument("--google-search-history", action="append")
    parser.add_argument("--ott-root", action="append")
    parser.add_argument("--prepare-dir")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    payloads, meta = asyncio.run(prepare_payloads(args))

    if args.prepare_dir:
        prepare_dir = Path(args.prepare_dir).expanduser().resolve()
        payload_dir = prepare_dir / "payloads"
        payload_dir.mkdir(parents=True, exist_ok=True)
        for index, payload in enumerate(payloads, 1):
            filename = f"{index:03d}-{payload['source_type']}.json"
            (payload_dir / filename).write_text(json.dumps(payload, indent=2))
        (prepare_dir / "meta.json").write_text(json.dumps(meta, indent=2, sort_keys=True))
        print(json.dumps({"status": "prepared", **meta}, indent=2))
        return

    if not payloads:
        print(json.dumps({"status": "noop", **meta}, indent=2))
        return

    results = asyncio.run(post_payloads(payloads))
    print(json.dumps({"status": "completed", "results": results, **meta}, indent=2))


if __name__ == "__main__":
    main()
