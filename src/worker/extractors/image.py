"""Image text extraction via Claude Haiku 4.5 vision (OCR)."""

from src.agents.base import agent_vision_call


async def extract_image(file_path: str, mime_type: str = "image/png", session=None) -> str:
    """Extract text from an image using Claude vision.

    Falls back to a placeholder if no DB session is available (audit can't be logged).
    """
    with open(file_path, "rb") as f:
        image_data = f.read()

    if session is None:
        # Can't log audit without session; return basic placeholder
        return "[Image uploaded — OCR requires database session]"

    result = await agent_vision_call(
        session,
        agent_name="ingestor",
        action="ocr",
        image_data=image_data,
        media_type=mime_type,
        prompt="Extract ALL text from this image. If it's a handwritten note or planner, "
        "transcribe everything you can read. Return only the extracted text, no commentary.",
    )

    return result["text"]
