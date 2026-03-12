"""Apple Notes exporter and collector payload builder."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import subprocess
from pathlib import Path

import httpx

from src.collector.agent_history import redact_text
from src.collector.main import load_state, save_state, stable_id
from src.config import settings

NOTE_EXPORT_SCRIPT = r"""
ObjC.import('Foundation');

function isoOrNull(value) {
  try {
    return value ? value.toISOString() : null;
  } catch (error) {
    return null;
  }
}

function folderExcluded(name, excludes) {
  var lowered = String(name || "").toLowerCase();
  return excludes.indexOf(lowered) >= 0;
}

function noteRecord(accountName, folderName, note) {
  return {
    id: String(note.id()),
    title: String(note.name() || "Untitled note"),
    body: String(note.body() || ""),
    account: String(accountName || ""),
    folder: String(folderName || ""),
    created_at: isoOrNull(note.creationDate()),
    updated_at: isoOrNull(note.modificationDate()),
  };
}

function collectFolderNotes(accountName, folder, excludes, bucket) {
  var folderName = String(folder.name() || "");
  if (folderExcluded(folderName, excludes)) {
    return;
  }
  folder.notes().forEach(function(note) {
    try {
      bucket.push(noteRecord(accountName, folderName, note));
    } catch (error) {}
  });
  folder.folders().forEach(function(child) {
    collectFolderNotes(accountName, child, excludes, bucket);
  });
}

function run(argv) {
  var app = Application('Notes');
  var excludes = [];
  try {
    excludes = JSON.parse($.getenv('APPLE_NOTES_EXCLUDES') || '[]').map(function(item) {
      return String(item || '').toLowerCase();
    });
  } catch (error) {}

  var records = [];
  app.accounts().forEach(function(account) {
    var accountName = String(account.name() || "");
    account.folders().forEach(function(folder) {
      collectFolderNotes(accountName, folder, excludes, records);
    });
  });
  return JSON.stringify(records);
}
"""


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", (value or "").strip()).strip("-").lower()
    return cleaned or "untitled"


def _parse_excluded_folders(raw_value: str | None = None) -> list[str]:
    value = raw_value if raw_value is not None else settings.apple_notes_exclude_folders
    return [item.strip().lower() for item in (value or "").split(",") if item.strip()]


def _html_to_markdown(value: str) -> str:
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
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
        text = text.replace(source.upper(), target)
    text = re.sub(r"<li[^>]*>", "- ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _note_markdown(note: dict) -> str:
    title = str(note.get("title") or "Untitled note").strip()
    body_text = _html_to_markdown(str(note.get("body") or ""))
    lines = [f"# {title}"]
    if note.get("account"):
        lines.append(f"Account: {note['account']}")
    if note.get("folder"):
        lines.append(f"Folder: {note['folder']}")
    if note.get("updated_at"):
        lines.append(f"Updated: {note['updated_at']}")
    lines.extend(["", body_text or "[empty note]"])
    return "\n".join(lines).strip()


def fetch_notes_snapshot(*, exclude_folders: list[str] | None = None) -> list[dict]:
    env = {
        **os.environ,
        "APPLE_NOTES_EXCLUDES": json.dumps(exclude_folders or _parse_excluded_folders()),
    }
    result = subprocess.run(
        ["osascript", "-l", "JavaScript"],
        input=NOTE_EXPORT_SCRIPT,
        capture_output=True,
        check=True,
        text=True,
        env=env,
    )
    payload = json.loads(result.stdout or "[]")
    return [item for item in payload if isinstance(item, dict)]


def snapshot_notes(notes: list[dict], export_root: Path) -> list[Path]:
    export_root.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    manifest: list[dict] = []
    for note in notes:
        account = _slugify(str(note.get("account") or "icloud"))
        folder = _slugify(str(note.get("folder") or "notes"))
        title = str(note.get("title") or "Untitled note").strip()
        note_id = str(note.get("id") or stable_id(title))
        directory = export_root / account / folder
        directory.mkdir(parents=True, exist_ok=True)
        stem = f"{_slugify(title)}-{stable_id(note_id)}"
        markdown_path = directory / f"{stem}.md"
        metadata_path = directory / f"{stem}.json"

        markdown_text = _note_markdown(note)
        metadata = {
            "id": note_id,
            "title": title,
            "account": note.get("account"),
            "folder": note.get("folder"),
            "created_at": note.get("created_at"),
            "updated_at": note.get("updated_at"),
            "content_hash": hashlib.sha256(markdown_text.encode("utf-8")).hexdigest(),
        }
        markdown_path.write_text(markdown_text)
        metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True))
        manifest.append({"markdown_path": str(markdown_path), "metadata_path": str(metadata_path), **metadata})
        written.append(markdown_path)

    (export_root / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return written


def collect_exported_entries(
    export_root: Path,
    state: dict,
    mode: str,
) -> tuple[list[dict], dict]:
    entries: list[dict] = []
    previous_entries = state.get("entries", {})
    next_state = {"entries": {}}

    for metadata_path in sorted(export_root.rglob("*.json")):
        if metadata_path.name == "manifest.json":
            continue
        markdown_path = metadata_path.with_suffix(".md")
        if not markdown_path.exists():
            continue
        metadata = json.loads(metadata_path.read_text())
        markdown_text = markdown_path.read_text()
        content_hash = hashlib.sha256(markdown_text.encode("utf-8")).hexdigest()
        state_key = metadata.get("id") or str(markdown_path)
        next_state["entries"][state_key] = content_hash
        if mode == "sync" and previous_entries.get(state_key) == content_hash:
            continue

        safe_excerpt = redact_text(markdown_text[:600])
        entries.append(
            {
                "external_id": f"apple_notes:note:{metadata.get('id') or stable_id(str(markdown_path))}",
                "project_ref": metadata.get("folder") or metadata.get("title"),
                "title": metadata.get("title") or markdown_path.stem,
                "summary": safe_excerpt[:240],
                "category": "note",
                "entry_type": "apple_note",
                "body_markdown": safe_excerpt,
                "raw_body_markdown": markdown_text,
                "tags": ["apple-notes", "personal-note"],
                "source_links": [],
                "external_url": None,
                "happened_at": metadata.get("updated_at") or metadata.get("created_at"),
                "is_sensitive": True,
                "metadata": {
                    "sensitive": True,
                    "note_id": metadata.get("id"),
                    "account": metadata.get("account"),
                    "folder": metadata.get("folder"),
                    "snapshot_path": str(markdown_path),
                },
                "content_hash": content_hash,
            }
        )

    return entries, next_state


async def post_entries(entries: list[dict], *, mode: str) -> dict:
    headers = {
        "Authorization": f"Bearer {settings.api_token}",
    }
    payload = {
        "source_type": "apple_notes",
        "source_name": "apple-notes",
        "mode": mode,
        "device_name": settings.collector_device_name,
        "entries": entries,
    }
    async with httpx.AsyncClient(base_url=settings.collector_api_base_url, timeout=60) as client:
        response = await client.post("/api/ingest/collector", headers=headers, json=payload)
        response.raise_for_status()
        return response.json()


def prepare_payload_bundle(
    mode: str,
    *,
    export_root: Path | None = None,
    skip_export: bool = False,
) -> tuple[dict, dict, Path]:
    export_path = (export_root or Path(settings.apple_notes_export_path)).expanduser().resolve()
    if not skip_export:
        notes = fetch_notes_snapshot(exclude_folders=_parse_excluded_folders())
        snapshot_notes(notes, export_path)

    state_path = Path(settings.apple_notes_state_path).expanduser()
    state = load_state(state_path)
    entries, next_state = collect_exported_entries(export_path, state, mode)
    payload = {
        "source_type": "apple_notes",
        "source_name": "apple-notes",
        "mode": mode,
        "device_name": settings.collector_device_name,
        "entries": entries,
    }
    return payload, next_state, state_path


async def run(mode: str, *, skip_export: bool = False) -> dict:
    payload, next_state, state_path = prepare_payload_bundle(mode, skip_export=skip_export)
    if not payload["entries"]:
        save_state(state_path, next_state)
        return {"status": "noop", "items_seen": 0}

    response = await post_entries(payload["entries"], mode=mode)
    save_state(state_path, next_state)
    return response


def main() -> None:
    parser = argparse.ArgumentParser(description="Apple Notes collector")
    parser.add_argument("mode", choices=["bootstrap", "sync"])
    parser.add_argument("--prepare-dir", dest="prepare_dir")
    parser.add_argument("--skip-export", action="store_true")
    args = parser.parse_args()

    if args.prepare_dir:
        payload, next_state, state_path = prepare_payload_bundle(args.mode, skip_export=args.skip_export)
        prepare_dir = Path(args.prepare_dir).expanduser().resolve()
        prepare_dir.mkdir(parents=True, exist_ok=True)
        (prepare_dir / "payload.json").write_text(json.dumps(payload, indent=2))
        (prepare_dir / "next_state.json").write_text(json.dumps(next_state, indent=2, sort_keys=True))
        (prepare_dir / "meta.json").write_text(
            json.dumps(
                {
                    "mode": args.mode,
                    "items_seen": len(payload["entries"]),
                    "state_path": str(state_path),
                    "device_name": payload["device_name"],
                    "source_name": payload["source_name"],
                    "source_type": payload["source_type"],
                },
                indent=2,
            )
        )
        print(json.dumps({"status": "prepared", "items_seen": len(payload["entries"])}))
        return

    import asyncio

    result = asyncio.run(run(args.mode, skip_export=args.skip_export))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
