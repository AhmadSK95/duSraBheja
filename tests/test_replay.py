from __future__ import annotations

from types import SimpleNamespace

from src.bot import replay


def test_artifact_needs_replay_for_missing_inbox_receipt() -> None:
    artifact = SimpleNamespace(metadata_={})

    assert replay.artifact_needs_replay(
        artifact=artifact,
        channel_name="inbox",
        has_final_classification=True,
    ) is True


def test_artifact_needs_replay_for_missing_planner_card() -> None:
    artifact = SimpleNamespace(metadata_={"discord_receipt_message_id": "123"})

    assert replay.artifact_needs_replay(
        artifact=artifact,
        channel_name="daily-planner",
        has_final_classification=True,
    ) is True


def test_should_skip_empty_message_for_blank_planner_without_image() -> None:
    message = SimpleNamespace(
        content="",
        attachments=[SimpleNamespace(content_type="application/pdf")],
    )

    assert replay.should_skip_empty_message(message, channel_name="daily-planner") is True


def test_should_skip_empty_message_accepts_text_only_planner() -> None:
    message = SimpleNamespace(
        content="Call contractor\nFinish duSraBheja review",
        attachments=[],
    )

    assert replay.should_skip_empty_message(message, channel_name="daily-planner") is False
