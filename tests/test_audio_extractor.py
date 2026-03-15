from __future__ import annotations

from pathlib import Path

import pytest

from src.worker.extractors import audio as audio_extractor


@pytest.mark.asyncio
async def test_extract_audio_retries_with_transcoded_wav(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "recording.m4a"
    source.write_bytes(b"original-audio")
    transcode_dir = tmp_path / "transcoded"
    transcode_dir.mkdir()
    transcoded = transcode_dir / "recording.wav"
    transcoded.write_bytes(b"transcoded-audio")

    calls: list[str] = []

    class FakeBadRequest(Exception):
        pass

    async def fake_transcribe(file_path: str) -> str:
        calls.append(file_path)
        if len(calls) == 1:
            raise FakeBadRequest("Invalid file format. Supported formats: ['wav']")
        return "transcribed text"

    async def fake_transcode(file_path: str) -> str:
        assert file_path == str(source)
        return str(transcoded)

    monkeypatch.setattr(audio_extractor.openai, "BadRequestError", FakeBadRequest)
    monkeypatch.setattr(audio_extractor, "_transcribe_audio_file", fake_transcribe)
    monkeypatch.setattr(audio_extractor, "_transcode_audio_file", fake_transcode)

    result = await audio_extractor.extract_audio(str(source))

    assert result == "transcribed text"
    assert calls == [str(source), str(transcoded)]
    assert not transcoded.exists()


def test_should_retry_audio_transcode_only_for_format_errors() -> None:
    assert audio_extractor._should_retry_audio_transcode(Exception("Invalid file format")) is True
    assert audio_extractor._should_retry_audio_transcode(Exception("Supported formats: ['wav']")) is True
    assert audio_extractor._should_retry_audio_transcode(Exception("rate limit exceeded")) is False
