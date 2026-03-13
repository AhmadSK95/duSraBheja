"""Capture intent inference and validation helpers."""

from __future__ import annotations

import re

from src.constants import CAPTURE_INTENTS, VALIDATION_STATUSES
from src.services.planner import validate_planner_capture

_QUESTION_PREFIX = re.compile(
    r"^\s*(what|why|how|when|where|who|is|are|can|could|should|would|do|does|did|will)\b",
    re.IGNORECASE,
)
_CRITIQUE_PATTERN = re.compile(
    r"\b(should not|wrong|incorrect|doesn['’]t make sense|bug|issue|not right|fix this|don['’]t like)\b",
    re.IGNORECASE,
)
_STATUS_PATTERN = re.compile(
    r"\b(finished|shipped|deployed|implemented|fixed|working on|coming along|completed|started)\b",
    re.IGNORECASE,
)


def normalize_capture_intent(intent: str | None, *, default: str = "thought") -> str:
    cleaned = (intent or "").strip().lower().replace(" ", "_")
    if cleaned not in CAPTURE_INTENTS:
        return default
    return cleaned


def normalize_validation_status(status: str | None, *, default: str = "validated") -> str:
    cleaned = (status or "").strip().lower().replace(" ", "_")
    if cleaned not in VALIDATION_STATUSES:
        return default
    return cleaned


def infer_capture_intent(
    text: str,
    *,
    category: str,
    suggested_intent: str | None = None,
) -> tuple[str, float]:
    normalized_suggested = normalize_capture_intent(suggested_intent, default="")
    if normalized_suggested:
        return normalized_suggested, 0.85

    lowered = (text or "").strip()
    if category in {"daily_planner", "weekly_planner"}:
        return "plan_capture", 0.98
    if category == "idea":
        return "idea", 0.96
    if category == "reminder":
        return "reminder_request", 0.97
    if _CRITIQUE_PATTERN.search(lowered):
        return "critique", 0.88
    if "?" in lowered or _QUESTION_PREFIX.search(lowered):
        return "question", 0.84
    if category in {"resource", "people"}:
        return "reference", 0.78
    if _STATUS_PATTERN.search(lowered):
        return "status_update", 0.72
    return "thought", 0.62


def validate_capture(
    *,
    text: str,
    category: str,
    entities: list[dict] | None = None,
    content_type: str | None = None,
) -> dict:
    if category in {"daily_planner", "weekly_planner"}:
        return validate_planner_capture(
            text,
            category=category,
            entities=entities,
            content_type=content_type,
        )

    cleaned = " ".join((text or "").split()).strip()
    if content_type == "image" and len(cleaned) < 20:
        return {
            "validation_status": "needs_review",
            "quality_issues": [
                {
                    "code": "ocr_sparse",
                    "severity": "error",
                    "message": "OCR output is too sparse to trust.",
                }
            ],
            "eligible_for_boards": False,
            "eligible_for_project_state": False,
        }

    return {
        "validation_status": "validated",
        "quality_issues": [],
        "eligible_for_boards": True,
        "eligible_for_project_state": True,
    }
