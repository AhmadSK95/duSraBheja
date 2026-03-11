from src.constants import normalize_category, normalize_tags


def test_normalize_category_maps_legacy_planner() -> None:
    assert normalize_category("planner") == "daily_planner"


def test_normalize_category_falls_back_to_note_for_unknown_values() -> None:
    assert normalize_category("something-else") == "note"


def test_normalize_tags_deduplicates_and_normalizes() -> None:
    assert normalize_tags([" Agent Work ", "agent work", "Progress"]) == ["agent-work", "progress"]
