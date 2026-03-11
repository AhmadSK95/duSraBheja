"""Utilities for parsing JSON objects from LLM text responses."""

from __future__ import annotations

import json
import re


class LLMJSONError(ValueError):
    """Raised when a model response cannot be parsed into a JSON object."""


_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)


def _balanced_json_object(text: str) -> str | None:
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False

    for index in range(start, len(text)):
        char = text[index]

        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]

    return None


def parse_json_object(text: str) -> dict:
    """Parse a JSON object from raw model text, handling fences and extra prose."""
    raw = (text or "").strip()
    if not raw:
        raise LLMJSONError("Model returned an empty response")

    candidates: list[str] = [raw]
    candidates.extend(match.group(1).strip() for match in _CODE_FENCE_RE.finditer(raw))

    balanced = _balanced_json_object(raw)
    if balanced:
        candidates.append(balanced.strip())

    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    raise LLMJSONError(f"Model response did not contain a valid JSON object: {raw[:200]!r}")
