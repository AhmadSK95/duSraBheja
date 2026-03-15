"""Audio transcription via OpenAI Whisper API with transcode fallback."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path

import openai

from src.config import settings

client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
log = logging.getLogger("brain-worker.audio")


async def _transcribe_audio_file(file_path: str) -> str:
    with open(file_path, "rb") as file_handle:
        transcript = await client.audio.transcriptions.create(
            model=settings.whisper_model,
            file=file_handle,
            response_format="text",
        )
    return transcript.strip()


def _should_retry_audio_transcode(exc: Exception) -> bool:
    message = str(exc).lower()
    return "invalid file format" in message or "supported formats" in message


async def _transcode_audio_file(file_path: str) -> str:
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        raise RuntimeError("ffmpeg is not installed in the worker image")

    temp_dir = tempfile.mkdtemp(prefix="brain-audio-")
    output_path = os.path.join(temp_dir, f"{Path(file_path).stem}.wav")

    process = await asyncio.create_subprocess_exec(
        ffmpeg_path,
        "-y",
        "-i",
        file_path,
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        output_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(stderr.decode("utf-8", errors="ignore").strip() or "ffmpeg failed")

    return output_path


async def extract_audio(file_path: str) -> str:
    """Transcribe audio file using Whisper, retrying with WAV transcode when needed."""
    try:
        return await _transcribe_audio_file(file_path)
    except openai.BadRequestError as exc:
        if not _should_retry_audio_transcode(exc):
            raise
        log.warning("Retrying audio transcription via ffmpeg WAV transcode for %s: %s", file_path, exc)

    transcoded_path: str | None = None
    try:
        transcoded_path = await _transcode_audio_file(file_path)
        return await _transcribe_audio_file(transcoded_path)
    finally:
        if transcoded_path:
            shutil.rmtree(os.path.dirname(transcoded_path), ignore_errors=True)
