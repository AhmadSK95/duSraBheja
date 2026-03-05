"""Plain text / markdown / CSV / JSON passthrough extractor."""

import aiofiles


async def extract_text(file_path: str) -> str:
    """Read file contents as text."""
    async with aiofiles.open(file_path, "r", encoding="utf-8", errors="replace") as f:
        content = await f.read()
    return content.strip()
