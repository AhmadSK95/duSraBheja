"""MIME type → extractor dispatcher."""

from src.worker.extractors.text import extract_text
from src.worker.extractors.pdf import extract_pdf
from src.worker.extractors.image import extract_image
from src.worker.extractors.audio import extract_audio
from src.worker.extractors.excel import extract_excel

MIME_MAP = {
    "text/plain": extract_text,
    "text/markdown": extract_text,
    "text/csv": extract_text,
    "text/html": extract_text,
    "application/json": extract_text,
    "application/pdf": extract_pdf,
    "image/png": extract_image,
    "image/jpeg": extract_image,
    "image/gif": extract_image,
    "image/webp": extract_image,
    "audio/mpeg": extract_audio,
    "audio/mp4": extract_audio,
    "audio/ogg": extract_audio,
    "audio/wav": extract_audio,
    "audio/webm": extract_audio,
    "audio/x-m4a": extract_audio,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": extract_excel,
    "application/vnd.ms-excel": extract_excel,
}


async def extract(file_path: str, mime_type: str, session=None) -> str:
    """Extract text from a file based on its MIME type.

    Args:
        file_path: Path to the downloaded file
        mime_type: MIME type of the file
        session: DB session (needed for image OCR audit logging)

    Returns:
        Extracted text as a string
    """
    # Normalize mime type
    mime = mime_type.split(";")[0].strip().lower()

    extractor = MIME_MAP.get(mime)
    if extractor is None:
        # Fallback: try text extraction
        if mime.startswith("text/"):
            extractor = extract_text
        else:
            return f"[Unsupported file type: {mime}]"

    # Image extractor needs session for audit logging
    if extractor == extract_image:
        return await extractor(file_path, mime, session=session)

    return await extractor(file_path)
