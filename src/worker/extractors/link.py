"""URL extraction and linked-page text capture."""

from __future__ import annotations

import re
import tempfile
from html import unescape
from pathlib import Path

import httpx

from src.worker.extractors.audio import extract_audio
from src.worker.extractors.docx import extract_docx
from src.worker.extractors.excel import extract_excel
from src.worker.extractors.pdf import extract_pdf
from src.worker.extractors.text import extract_text

MAX_LINK_TEXT_CHARS = 8_000
URL_RE = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)


def extract_urls_from_text(text: str) -> list[str]:
    """Return de-duplicated URLs in the order they appear."""
    seen: set[str] = set()
    urls: list[str] = []
    for match in URL_RE.findall(text or ""):
        cleaned = match.rstrip(".,;:!?)\"]}'")
        if cleaned not in seen:
            seen.add(cleaned)
            urls.append(cleaned)
    return urls


def _strip_html(html: str) -> str:
    cleaned = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    cleaned = re.sub(r"(?i)</(p|div|section|article|li|h1|h2|h3|h4|h5|h6|br)>", "\n", cleaned)
    cleaned = re.sub(r"(?s)<[^>]+>", " ", cleaned)
    cleaned = unescape(cleaned)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _extract_html_metadata(html: str, url: str) -> str:
    title_match = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
    desc_match = re.search(
        r'(?is)<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
        html,
    )
    title = unescape(title_match.group(1).strip()) if title_match else url
    description = unescape(desc_match.group(1).strip()) if desc_match else ""
    body = _strip_html(html)[:MAX_LINK_TEXT_CHARS]

    sections = [f"# Linked Resource", f"URL: {url}", f"Title: {title}"]
    if description:
        sections.extend(["", "Description:", description])
    if body:
        sections.extend(["", "Content:", body])
    return "\n".join(sections).strip()


async def extract_url(url: str) -> str:
    """Fetch a URL and return a classification-friendly text snapshot."""
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        response = await client.get(url, headers={"User-Agent": "duSraBheja/2.0"})
        response.raise_for_status()

    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type in {"text/plain", "text/markdown", "text/csv", "application/json"}:
        body = response.text[:MAX_LINK_TEXT_CHARS]
        return f"# Linked Resource\nURL: {url}\n\nContent:\n{body}".strip()

    if content_type in {"text/html", "application/xhtml+xml"} or not content_type:
        return _extract_html_metadata(response.text, url)

    suffix_map = {
        "application/pdf": ".pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        "application/vnd.ms-excel": ".xls",
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "audio/ogg": ".ogg",
        "audio/wav": ".wav",
        "audio/webm": ".webm",
        "audio/x-m4a": ".m4a",
    }
    suffix = suffix_map.get(content_type)
    if not suffix:
        return f"# Linked Resource\nURL: {url}\n\n[Unsupported linked content type: {content_type or 'unknown'}]"

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
        handle.write(response.content)
        temp_path = Path(handle.name)

    try:
        if content_type == "application/pdf":
            extracted = await extract_pdf(str(temp_path))
        elif content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            extracted = await extract_docx(str(temp_path))
        elif "excel" in content_type or "spreadsheet" in content_type:
            extracted = await extract_excel(str(temp_path))
        elif content_type.startswith("audio/"):
            extracted = await extract_audio(str(temp_path))
        else:
            extracted = await extract_text(str(temp_path))
    finally:
        temp_path.unlink(missing_ok=True)

    extracted = (extracted or "")[:MAX_LINK_TEXT_CHARS]
    return f"# Linked Resource\nURL: {url}\n\nContent:\n{extracted}".strip()
