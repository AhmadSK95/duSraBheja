"""Image text extraction via Claude Haiku 4.5 vision (OCR)."""

from src.agents.base import agent_vision_call
from src.lib.store import list_active_project_aliases


def _build_ocr_prompt(project_aliases: list[str]) -> str:
    alias_lines = "\n".join(f"- {alias}" for alias in project_aliases[:25])
    alias_block = f"\nKnown active project names and aliases you may see:\n{alias_lines}\n" if alias_lines else ""
    return (
        "Extract ALL text from this image as clean plain text. "
        "If it is a handwritten planner or whiteboard, preserve dates and put each task or bullet on its own line. "
        "Use the known project names below to resolve ambiguous handwriting when a word is close to one of them. "
        "Do not invent content. If a word is uncertain, prefer the closest visible text, but bias toward the known project names when the handwriting matches. "
        "Do not guess a missing year or expand a partial date into a full date unless the digits are clearly visible. "
        "If the page appears to cover a single day, keep it as a single-day capture rather than inventing a weekly heading.\n"
        f"{alias_block}"
        "Return only the extracted text, with no commentary or markdown fences."
    )


async def extract_image(file_path: str, mime_type: str = "image/png", session=None) -> str:
    """Extract text from an image using Claude vision.

    Falls back to a placeholder if no DB session is available (audit can't be logged).
    """
    with open(file_path, "rb") as f:
        image_data = f.read()

    if session is None:
        # Can't log audit without session; return basic placeholder
        return "[Image uploaded — OCR requires database session]"

    project_aliases = await list_active_project_aliases(session, limit=20)

    result = await agent_vision_call(
        session,
        agent_name="ingestor",
        action="ocr",
        image_data=image_data,
        media_type=mime_type,
        prompt=_build_ocr_prompt(project_aliases),
    )

    return result["text"]
