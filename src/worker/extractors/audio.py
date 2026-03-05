"""Audio transcription via OpenAI Whisper API."""

import openai

from src.config import settings

client = openai.AsyncOpenAI(api_key=settings.openai_api_key)


async def extract_audio(file_path: str) -> str:
    """Transcribe audio file using Whisper."""
    with open(file_path, "rb") as f:
        transcript = await client.audio.transcriptions.create(
            model=settings.whisper_model,
            file=f,
            response_format="text",
        )

    return transcript.strip()
