from __future__ import annotations

from types import SimpleNamespace

from src.bot import replay


def test_artifact_needs_replay_only_when_unclassified() -> None:
    artifact = SimpleNamespace(metadata_={})

    assert replay.artifact_needs_replay(
        artifact=artifact,
        channel_name="inbox",
        has_any_classification=False,
    ) is True
    assert replay.artifact_needs_replay(
        artifact=artifact,
        channel_name="inbox",
        has_any_classification=True,
    ) is False


def test_should_skip_empty_message_for_blank_inbox_without_attachments() -> None:
    message = SimpleNamespace(content="", attachments=[])

    assert replay.should_skip_empty_message(message, channel_name="inbox") is True


def test_should_skip_empty_message_accepts_image_in_inbox() -> None:
    message = SimpleNamespace(
        content="",
        attachments=[SimpleNamespace(content_type="image/png")],
    )

    assert replay.should_skip_empty_message(message, channel_name="inbox") is False
