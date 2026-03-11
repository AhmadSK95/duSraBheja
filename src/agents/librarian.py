"""Librarian agent — merges artifacts into canonical notes."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.base import agent_call
from src.config import settings
from src.lib.llm_json import parse_json_object

SYSTEM_PROMPT = """You are the Librarian agent for a personal second brain.

Your job is to maintain canonical knowledge notes. When new information arrives about
a person, project, or topic that already has a note, you MERGE the new info into the
existing note intelligently.

When merging:
- Preserve all existing information
- Add new facts without duplicating
- Update outdated info if the new input clearly supersedes it
- Keep the note organized with clear sections
- Use markdown formatting

When creating a new note:
- Give it a clear, descriptive title
- Organize content logically
- Include all extracted entities and facts

Return a JSON object:
{
  "action": "create" or "update",
  "title": "Note title",
  "content": "Full markdown content of the note",
  "tags": ["relevant", "tags"]
}

Return ONLY valid JSON, no markdown fences."""


async def process_artifact(
    session: AsyncSession,
    artifact_text: str,
    classification: dict,
    existing_note_content: str | None = None,
    trace_id: uuid.UUID | None = None,
) -> dict:
    """Decide whether to create or update a canonical note.

    Returns dict with action, title, content, tags.
    """
    if existing_note_content:
        prompt = f"""New input classified as [{classification["category"]}]:
"{artifact_text}"

Classification details:
- Entities: {json.dumps(classification.get("entities", []))}
- Tags: {classification.get("tags", [])}
- Summary: {classification.get("summary", "")}

Existing note content:
{existing_note_content}

Merge the new information into the existing note."""
    else:
        prompt = f"""New input classified as [{classification["category"]}]:
"{artifact_text}"

Classification details:
- Entities: {json.dumps(classification.get("entities", []))}
- Tags: {classification.get("tags", [])}
- Summary: {classification.get("summary", "")}

Create a new canonical note for this."""

    result = await agent_call(
        session,
        agent_name="librarian",
        action="merge" if existing_note_content else "create",
        prompt=prompt,
        system=SYSTEM_PROMPT,
        model=settings.sonnet_model,
        max_tokens=4096,
        temperature=0.1,
        trace_id=trace_id,
    )

    parsed = parse_json_object(result["text"])
    return {
        "action": parsed.get("action", "create"),
        "title": parsed.get("title", "Untitled"),
        "content": parsed.get("content", artifact_text),
        "tags": parsed.get("tags") or [],
    }
