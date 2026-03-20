"""Website builder agent — understands requests, plans changes, generates code edits."""

from __future__ import annotations

import json
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.base import agent_call
from src.config import settings
from src.lib.llm_json import LLMJSONError, parse_json_object

WEBSITE_BUILDER_SYSTEM_PROMPT = """\
You are the website builder for Ahmad's personal site.
You have two ways to make changes:

1. CONTENT changes (instant): Create/update/delete WebsiteSection records in the database.
   Section types: hero, text_block, stat_band, card_grid, interests_bar, project_grid,
   case_study, photo_row, chat_shell, custom_html, photo_break, story_block.

2. CODE changes (requires deploy): Modify CSS (site.css), page handlers (public.py),
   JavaScript (site.js), or templates.

Given a request, produce a JSON change plan:
{
  "tier": "content" | "code" | "both",
  "explanation": "what you'll do and why",
  "content_changes": [
    {"action": "create" | "update" | "delete", "page": "...", "section_key": "...",
     "section_type": "...", "sort_order": 0, "title": "...",
     "content": {}, "style_hints": {}}
  ],
  "code_changes": [
    {"file": "src/api/static/public/site.css", "description": "...", "diff_hint": "..."}
  ]
}

Section type content schemas:
- hero: {heading, subline, cta_primary: {label, href},
         cta_secondary: {label, href}, photo_key, sticker_tilt}
- text_block: {kicker, heading, body, photo_key, sticker_tilt, link: {label, href}}
- stat_band: {metrics: [{number, label}]}
- card_grid: {kicker, heading, cards: [{title, body, cta_label, cta_href, accent}]}
- interests_bar: {kicker, items: [{label, icon}]}
- project_grid: {kicker, heading, featured_slug, max_items}
- case_study: {project_slug}
- photo_row: {photo_keys: [str], sticker_tilts: [str]}
- chat_shell: {heading, intro_text, starter_prompts: [str]}
- custom_html: {html}
- photo_break: {photo_key, caption}
- story_block: {kicker, heading, body, photo_key, reverse}

Write in Ahmad's voice: direct, confident, opinionated. Make design decisions.
You have access to the current site state and Ahmad's taste profile.
Pages: home, work, brain, connect.
"""

CODE_EDIT_SYSTEM_PROMPT = """You generate precise file edits for a website.

Given a change plan and the current file contents, produce a JSON array of edits:
[
  {
    "file": "path/to/file",
    "old_string": "exact text to find in the file",
    "new_string": "replacement text"
  }
]

Rules:
- old_string must be an exact substring of the current file content.
- Keep edits minimal and targeted.
- Maintain the existing code style.
- Return ONLY valid JSON array.
"""

CASE_STUDY_SYSTEM_PROMPT = """You synthesize a project case study from evidence.

Return ONLY valid JSON:
{
  "motivation": "why this project exists — the problem it solves",
  "architecture_description": "how it works, written for a technical audience",
  "key_decisions": [
    {"decision": "what was decided", "context": "why", "tradeoff": "what was given up"}
  ],
  "struggles": [
    {"problem": "what went wrong", "resolution": "how it was solved or worked around"}
  ],
  "learnings": ["concrete takeaway"],
  "stack_rationale": [
    {"tech": "technology name", "why": "why it was chosen over alternatives"}
  ]
}

Rules:
- Stay grounded in the evidence. Do not invent decisions or struggles.
- Write for a technical reader who wants depth, not marketing copy.
- If evidence is thin for a section, say so explicitly.
"""

JSON_REPAIR_SYSTEM_PROMPT = """You repair malformed JSON.

Return ONLY valid JSON.
Do not wrap in markdown fences.
Do not add commentary.
Preserve the original schema and content as closely as possible.
"""


async def plan_website_change(
    session: AsyncSession,
    *,
    instruction: str,
    current_sections: list[dict],
    taste_profile: str | None = None,
    current_css: str | None = None,
    trace_id: uuid.UUID | None = None,
) -> dict:
    """Analyze a website modification request and produce a structured change plan."""
    sections_summary = json.dumps(current_sections, default=str, indent=2)[:4000]
    css_snippet = (current_css or "")[:2000]

    prompt = f"""Instruction: {instruction}

Current site sections:
{sections_summary}

Taste/voice profile:
{taste_profile or "Direct, confident, technical. Teal/gold/purple dark palette."}

Current CSS (first 2000 chars):
{css_snippet}

Produce the JSON change plan."""

    result = await agent_call(
        session,
        agent_name="website_builder",
        action="plan_change",
        prompt=prompt,
        system=WEBSITE_BUILDER_SYSTEM_PROMPT,
        model=settings.sonnet_model,
        max_tokens=8000,
        temperature=0.2,
        trace_id=trace_id,
    )
    try:
        return parse_json_object(result["text"])
    except LLMJSONError:
        repair = await agent_call(
            session,
            agent_name="website_builder",
            action="repair_plan_json",
            prompt=f"Repair into valid JSON:\n\n{result['text']}",
            system=JSON_REPAIR_SYSTEM_PROMPT,
            model=settings.sonnet_model,
            max_tokens=8000,
            temperature=0.0,
            trace_id=trace_id,
        )
        return parse_json_object(repair["text"])


async def generate_code_edits(
    session: AsyncSession,
    *,
    plan: dict,
    file_contents: dict[str, str],
    trace_id: uuid.UUID | None = None,
) -> list[dict]:
    """Given a change plan and current file contents, produce precise code edits."""
    files_summary = ""
    for path, content in file_contents.items():
        files_summary += f"\n--- {path} ---\n{content[:3000]}\n"

    prompt = f"""Change plan:
{json.dumps(plan.get("code_changes", []), indent=2)}

Explanation: {plan.get("explanation", "")}

Current files:
{files_summary}

Produce the JSON array of edits."""

    result = await agent_call(
        session,
        agent_name="website_builder",
        action="generate_code_edits",
        prompt=prompt,
        system=CODE_EDIT_SYSTEM_PROMPT,
        model=settings.sonnet_model,
        max_tokens=4000,
        temperature=0.1,
        trace_id=trace_id,
    )
    text = result["text"].strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass
    # Try extracting from markdown fence
    obj = parse_json_object(text)
    if isinstance(obj, dict) and "edits" in obj:
        return obj["edits"]
    return []


async def synthesize_project_case_study(
    session: AsyncSession,
    *,
    project_name: str,
    evidence_text: str,
    use_opus: bool = False,
    trace_id: uuid.UUID | None = None,
) -> dict:
    """Produce a structured case study from project evidence."""
    prompt = f"""Project: {project_name}

Evidence:
{evidence_text}

Return the JSON case study."""

    model = settings.opus_model if use_opus else settings.sonnet_model
    result = await agent_call(
        session,
        agent_name="website_builder",
        action="synthesize_case_study",
        prompt=prompt,
        system=CASE_STUDY_SYSTEM_PROMPT,
        model=model,
        max_tokens=2400,
        temperature=0.15,
        trace_id=trace_id,
    )
    try:
        return parse_json_object(result["text"])
    except LLMJSONError:
        repair = await agent_call(
            session,
            agent_name="website_builder",
            action="repair_case_study_json",
            prompt=f"Repair into valid JSON:\n\n{result['text']}",
            system=JSON_REPAIR_SYSTEM_PROMPT,
            model=settings.sonnet_model,
            max_tokens=2400,
            temperature=0.0,
            trace_id=trace_id,
        )
        return parse_json_object(repair["text"])
