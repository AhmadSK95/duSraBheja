"""Structured public/profile narrative built from the CompanyInterviewPrep source pack."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.lib import store
from src.lib.time import format_display_datetime
from src.models import Artifact, Note, SourceItem, SyncSource
from src.services.profile_inventory import build_profile_inventory_payload

PUBLIC_MODEL_TTL = timedelta(minutes=20)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _public_seed_candidates() -> list[Path]:
    configured = Path(settings.public_profile_seed_path).expanduser()
    mounted = Path("/public-seed")
    repo_local = Path(__file__).resolve().parents[2] / "public-seed"
    candidates: list[Path] = []
    for candidate in (configured, mounted, repo_local):
        if candidate not in candidates:
            candidates.append(candidate)
    return candidates


def resolve_public_seed_path() -> Path:
    for candidate in _public_seed_candidates():
        if candidate.exists():
            return candidate
    return _public_seed_candidates()[0]


def _slugify(value: str | None) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower())
    return cleaned.strip("-")


def public_asset_path(filename: str) -> Path | None:
    safe_name = Path(filename).name
    if not safe_name or safe_name != filename:
        return None
    # Check website_photos first, then demo_videos
    for seed_dir in _public_seed_candidates():
        if not seed_dir.exists():
            continue
        for subdir in ("website_photos", "demo_videos"):
            candidate = seed_dir / subdir / safe_name
            if candidate.exists():
                return candidate
    return None


def public_asset_url(filename: str | None) -> str | None:
    if not filename:
        return None
    safe_name = Path(filename).name
    if not safe_name:
        return None
    return f"/public-assets/profile/{safe_name}"


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _extract_markdown_sections(text: str) -> list[tuple[int, str, str]]:
    lines = text.splitlines()
    sections: list[tuple[int, str, str]] = []
    current_level = 1
    current_title = "Document"
    buffer: list[str] = []
    for line in lines:
        match = re.match(r"^(#{1,6})\s+(.*)$", line.strip())
        if match:
            if buffer:
                sections.append((current_level, current_title, "\n".join(buffer).strip()))
            current_level = len(match.group(1))
            current_title = match.group(2).strip()
            buffer = []
            continue
        buffer.append(line)
    if buffer:
        sections.append((current_level, current_title, "\n".join(buffer).strip()))
    return [section for section in sections if section[2].strip()]


def _extract_labeled_block(text: str, label: str, *, stop_labels: list[str] | None = None) -> str:
    lines = (text or "").splitlines()
    normalized_label = label.lower()
    normalized_stops = {item.lower() for item in (stop_labels or [])}
    collected: list[str] = []
    capture = False
    for raw in lines:
        stripped = raw.strip()
        bold_match = re.match(r"^\*\*(.+?)\*\*\s*:?\s*$", stripped)
        heading = _compact(bold_match.group(1)) if bold_match else ""
        if heading.lower().startswith(normalized_label):
            capture = True
            continue
        if capture and heading:
            if any(heading.lower().startswith(stop) for stop in normalized_stops):
                break
        if capture:
            collected.append(raw)
    return "\n".join(collected).strip()


def _section_map(text: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for _level, title, body in _extract_markdown_sections(text):
        mapping[title.strip().lower()] = body.strip()
    return mapping


def _find_section_text(sections: dict[str, str], *needles: str) -> str:
    normalized = [_compact(item).lower() for item in needles if _compact(item)]
    if not normalized:
        return ""
    for needle in normalized:
        if needle in sections:
            return sections[needle]
    for key, value in sections.items():
        lowered = key.lower()
        if any(needle in lowered for needle in normalized):
            return value
    return ""


def _section_body(text: str, title: str) -> str:
    lines = (text or "").splitlines()
    target = _compact(title).lower()
    start_index: int | None = None
    level = 0
    for index, raw in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.*)$", raw.strip())
        if not match:
            continue
        heading = _compact(match.group(2)).lower()
        if heading == target:
            start_index = index + 1
            level = len(match.group(1))
            break
    if start_index is None:
        return ""

    collected: list[str] = []
    for raw in lines[start_index:]:
        match = re.match(r"^(#{1,6})\s+(.*)$", raw.strip())
        if match and len(match.group(1)) <= level:
            break
        collected.append(raw)
    return "\n".join(collected).strip()


def _strip_markdown(value: str | None) -> str:
    text = value or ""
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"^>\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _compact(value: str | None) -> str:
    return " ".join(_strip_markdown(value).split()).strip()


def _excerpt(value: str | None, *, limit: int = 320) -> str:
    cleaned = _compact(value)
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[: limit - 1].rstrip()}…"


def _bullet_lines(value: str | None) -> list[str]:
    lines: list[str] = []
    for raw in (value or "").splitlines():
        stripped = _strip_markdown(raw).strip()
        if stripped.startswith("- "):
            stripped = stripped[2:].strip()
        elif stripped.startswith("* "):
            stripped = stripped[2:].strip()
        elif re.match(r"^\d+\.\s+", stripped):
            stripped = re.sub(r"^\d+\.\s+", "", stripped)
        if stripped:
            lines.append(stripped)
    return lines


def _kv_lines(value: str | None) -> dict[str, str]:
    entries: dict[str, str] = {}
    for raw in (value or "").splitlines():
        stripped = raw.strip()
        if stripped.startswith("- "):
            stripped = stripped[2:].strip()
        match = re.match(r"^\*\*(.+?):\*\*\s*(.+)$", stripped) or re.match(
            r"^\*\*(.+?)\*\*:\s*(.+)$", stripped
        )
        if match:
            entries[_compact(match.group(1)).lower()] = _compact(match.group(2))
    return entries


def _table_rows(value: str | None) -> list[list[str]]:
    rows: list[list[str]] = []
    for raw in (value or "").splitlines():
        stripped = raw.strip()
        if not stripped.startswith("|") or stripped.count("|") < 2:
            continue
        if set(stripped.replace("|", "").replace("-", "").replace(" ", "")) == set():
            continue
        cells = [_compact(cell) for cell in stripped.strip("|").split("|")]
        rows.append(cells)
    return rows


def _split_paragraphs(value: str | None) -> list[str]:
    blocks = re.split(r"\n\s*\n", (value or "").strip())
    return [_compact(block) for block in blocks if _compact(block)]


def _find_url(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"https?://[^\s)]+", value)
    return match.group(0) if match else None


def _normalize_public_href(value: str | None) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        return ""
    if cleaned.startswith(("http://", "https://", "mailto:", "tel:")):
        return cleaned
    return f"https://{cleaned.lstrip('/')}"


@dataclass(slots=True)
class PhotoAsset:
    key: str
    filename: str
    title: str
    description: str
    vibe: str
    best_for: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["url"] = public_asset_url(self.filename)
        return payload


@dataclass(slots=True)
class LifeEra:
    slug: str
    title: str
    years: str
    summary: str
    highlights: list[str] = field(default_factory=list)
    institutions: list[str] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RoleExperience:
    slug: str
    title: str
    organization: str
    period: str
    location: str
    summary: str
    bullets: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ProjectCase:
    slug: str
    title: str
    tagline: str
    summary: str
    status: str
    tier: str = "flagship"
    stack: list[str] = field(default_factory=list)
    resume_bullets: list[str] = field(default_factory=list)
    body: str = ""
    demonstrates: list[str] = field(default_factory=list)
    links: list[dict[str, str]] = field(default_factory=list)
    proof: list[str] = field(default_factory=list)
    role_scope: str = ""
    constraints: list[str] = field(default_factory=list)
    outcomes: list[str] = field(default_factory=list)
    case_study_sections: list[str] = field(default_factory=list)
    demo_asset: str = ""
    display_order: int = 999
    curated_case_study: dict[str, Any] = field(default_factory=dict)
    daily_update_window: dict[str, Any] = field(default_factory=dict)
    supporting_evidence: list[dict[str, Any]] = field(default_factory=list)
    latest_work_summary: str = ""


@dataclass(slots=True)
class CapabilityBook:
    slug: str
    title: str
    summary: str
    chapters: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ContactMode:
    key: str
    label: str
    value: str
    href: str
    note: str


@dataclass(slots=True)
class CoverageGap:
    slug: str
    title: str
    severity: str
    summary: str
    recommendation: str


_CASE_STUDY_SECTION_ORDER = [
    "Project Framing",
    "Problem / Context",
    "Role and Ownership",
    "Constraints",
    "Architecture",
    "Key Decisions",
    "Iterations and Struggles",
    "Learnings",
    "Outcomes",
    "Next Improvements",
]

_PUBLIC_PROJECT_REGISTRY: dict[str, dict[str, Any]] = {
    "dusrabheja": {
        "title": "duSraBheja",
        "aliases": ["du-sra-bheja", "dusra-bheja", "duSraBheja"],
        "tier": "flagship",
        "order": 0,
        "demo_asset": "",
        "role_scope": (
            "I designed the system architecture, wrote the ingestion and public-surface code, "
            "shaped the agent prompts, and owned the deployment and operational guardrails."
        ),
        "constraints": [
            "The bot could not block on LLM or extraction work.",
            "Public answers had to stay hard-walled from the private brain.",
            "Everything needed to run cheaply on a single droplet.",
        ],
        "outcomes": [
            "A Discord-native second brain with MCP access, public profile surfaces, and agent bootstrapping.",
            "A production split between private knowledge and approved public facts.",
        ],
    },
    "datagenie": {
        "title": "dataGenie",
        "aliases": ["data-genie"],
        "tier": "flagship",
        "order": 1,
        "demo_asset": "datagenie_demo.mp4",
        "role_scope": (
            "I built the backend, query-routing logic, LLM provider layer, data profiling flow, "
            "and the product framing for how non-technical users ask analytical questions."
        ),
        "constraints": [
            "Simple questions needed fast direct answers instead of agent overhead.",
            "Complex analytical questions still needed decomposition and synthesis.",
            "The system had to stay usable even when one model provider failed.",
        ],
        "outcomes": [
            "A conversational analytics prototype that routes between direct SQL and agentic reasoning.",
            "A reusable provider abstraction with fallback between Claude, OpenAI, and local models.",
        ],
    },
    "balkan-barbershop-website": {
        "title": "Balkan Barbershop",
        "aliases": [
            "balkan-barbershop",
            "barbershop",
            "balkan-barbers",
            "balkan-barbers-barbershop-booking-platform",
        ],
        "tier": "flagship",
        "order": 2,
        "demo_asset": "balkan_barbers_demo.mp4",
        "role_scope": (
            "I owned the customer booking flow, backend APIs, payments, admin tooling, notifications, "
            "deployment, and the final editorial presentation of the brand."
        ),
        "constraints": [
            "The product had to work for a real shop's operations, not just look premium.",
            "Payments, reminders, and admin workflows all had to fit a lean single-owner setup.",
            "Infrastructure had to stay maintainable without a dedicated ops team.",
        ],
        "outcomes": [
            "A real booking platform with payments, admin tooling, and customer lifecycle flows.",
            "A cleaner DigitalOcean deployment setup after simplifying an overbuilt AWS path.",
        ],
    },
    "kaffa-espresso-bar-website": {
        "title": "Kaffa Espresso Bar",
        "aliases": [
            "kaffa",
            "kaffa-espresso-bar",
            "kaffa-espresso-bar-website-live-client-project",
        ],
        "tier": "flagship",
        "order": 3,
        "demo_asset": "kaffa_espresso_bar_demo.mp4",
        "role_scope": (
            "I handled the brand translation, site build, domain migration, HTTPS setup, deployment "
            "automation, and the production handoff for a real local business."
        ),
        "constraints": [
            "The client needed a polished web presence without ongoing platform complexity.",
            "Domain and hosting ownership were split across different systems.",
            "Deployment needed rollback safety because the site was the shop's public front door.",
        ],
        "outcomes": [
            "A live coffee-shop site on a custom domain with HTTPS, redirects, and git-driven releases.",
            "A repeatable small-business deployment workflow I can reuse with future clients.",
        ],
    },
    "teachassist-ai": {
        "title": "TeachAssist AI",
        "aliases": [
            "teacherai",
            "teachassist",
            "teacherai-intelligent-teacher-ecosystem",
            "teachassist-ai",
        ],
        "tier": "secondary",
        "order": 4,
        "demo_asset": "",
        "role_scope": (
            "I defined the product and technical architecture for an educator-facing AI workflow that "
            "generates differentiated classroom materials."
        ),
        "constraints": [
            "Outputs needed to be useful for teachers, not just visually plausible.",
            "Compliance and classroom sensitivity mattered alongside content generation speed.",
        ],
        "outcomes": [
            "A spec-rich monorepo MVP direction with strong testing and education-specific workflows.",
        ],
    },
    "ai-resume-matcher": {
        "title": "AI Resume Matcher",
        "aliases": [
            "resume-matcher",
            "resume-matcher-fresh",
            "resume-matcher-fresh-master-repo",
            "ai-resume-matcher",
        ],
        "tier": "secondary",
        "order": 5,
        "demo_asset": "",
        "role_scope": (
            "I built the retrieval and matching flow, grounded LLM analysis path, and the UX for "
            "natural-language candidate search across a real resume corpus."
        ),
        "constraints": [
            "Resume formats were messy and inconsistent.",
            "LLM analysis needed citations to avoid hallucinated candidate claims.",
        ],
        "outcomes": [
            "A grounded candidate-search MVP with vector retrieval, JD analysis, and export workflows.",
        ],
    },
}

_OPEN_BRAIN_TOPICS = [
    {
        "title": "Projects I've actually shipped",
        "summary": "duSraBheja, dataGenie, Balkan Barbershop, Kaffa, and why each one exists.",
    },
    {
        "title": "How I think about AI systems",
        "summary": "When I use agents, when I do not, and how I keep LLM-heavy systems honest.",
    },
    {
        "title": "Why I left Amazon",
        "summary": "The shift from scale-for-scale's-sake toward work I actually care about.",
    },
    {
        "title": "What my life looks like outside work",
        "summary": "Jersey City, Annie, Oscar and Iris, anime, Indian comedy, and music rabbit holes.",
    },
]

_FLAGSHIP_CASE_STUDY_OVERRIDES: dict[str, dict[str, Any]] = {
    "dusrabheja": {
        "hero_label": "Private Brain, Public Surface",
        "project_framing": (
            "duSraBheja exists because my work, ideas, notes, screenshots, PDFs, and session context were "
            "fragmented across too many tools. I did not want another note app. I wanted a system that could "
            "absorb raw evidence, turn it into memory, and then make that memory usable both for me and for "
            "the agents helping me work."
        ),
        "problem": (
            "Manual note-taking systems fail me for the same reason most knowledge systems fail: they depend on "
            "consistent human organization at the exact moment when I am busy doing something else. The real "
            "problem was not storage. It was ingestion, promotion, retrieval, and the split between private "
            "memory and public-safe narrative."
        ),
        "why_now": (
            "Once I started using coding agents seriously, copy-pasting context became the bottleneck. I needed "
            "my brain to be toolable so the AI could read relevant history mid-session instead of waiting for me "
            "to reconstruct it manually."
        ),
        "architecture_narrative": (
            "I designed the system as a set of asynchronous lanes instead of one giant chat loop. Discord is the "
            "capture surface. The bot only acknowledges and enqueues. Workers do the heavy lifting: extraction, "
            "classification, embeddings, librarian merge, and story/read-model generation. The private brain sits "
            "behind PostgreSQL, pgvector, and canonical notes. MCP, REST, and the public site all read from "
            "curated interfaces, not raw intake. That separation is what makes the public clone safe."
        ),
        "architecture_diagram": {
            "title": "Capture to Memory to Public Surface",
            "caption": "The public site never reads raw intake. It reads curated snapshots published from the private brain.",
            "lanes": [
                {
                    "label": "Capture",
                    "nodes": [
                        {"id": "cap_discord", "label": "Discord inbox", "detail": "text, files, voice, links"},
                        {"id": "cap_collectors", "label": "Collectors", "detail": "repo history, browser, notes"},
                    ],
                },
                {
                    "label": "Processing",
                    "nodes": [
                        {"id": "proc_queue", "label": "ARQ queue", "detail": "bot only enqueues"},
                        {"id": "proc_agents", "label": "Classifier + librarian", "detail": "extract, classify, merge"},
                        {"id": "proc_embed", "label": "Embeddings", "detail": "semantic retrieval"},
                    ],
                },
                {
                    "label": "Brain",
                    "nodes": [
                        {"id": "brain_store", "label": "Canonical notes", "detail": "Postgres + pgvector"},
                        {"id": "brain_models", "label": "Read models", "detail": "projects, story, dashboard"},
                    ],
                },
                {
                    "label": "Interfaces",
                    "nodes": [
                        {"id": "iface_mcp", "label": "MCP + API", "detail": "agent tooling"},
                        {"id": "iface_public", "label": "Curated public snapshots", "detail": "approved facts only"},
                    ],
                },
            ],
            "edges": [
                {"from": "cap_discord", "to": "proc_queue", "label": "enqueue"},
                {"from": "cap_collectors", "to": "proc_queue", "label": "sync"},
                {"from": "proc_queue", "to": "proc_agents", "label": "jobs"},
                {"from": "proc_agents", "to": "proc_embed", "label": "chunk + embed"},
                {"from": "proc_agents", "to": "brain_store", "label": "promote"},
                {"from": "proc_embed", "to": "brain_store", "label": "index"},
                {"from": "brain_store", "to": "brain_models", "label": "materialize"},
                {"from": "brain_models", "to": "iface_mcp", "label": "tool access"},
                {"from": "brain_models", "to": "iface_public", "label": "publish safe layer"},
            ],
            "callouts": [
                {
                    "label": "Safety wall",
                    "body": "The public clone and website only read approved facts and curated snapshots.",
                },
                {
                    "label": "Async boundary",
                    "body": "Discord never blocks on LLM or extraction work; workers absorb the cost and latency.",
                },
            ],
        },
        "key_decisions": [
            {
                "title": "Discord as the intake surface",
                "decision": "Use Discord as the always-open capture layer instead of building a custom UI first.",
                "rationale": "Capture needed to be frictionless immediately; Discord already matched how I actually drop thoughts and files.",
                "tradeoff": "I accepted less control over the initial UX to get better ingestion behavior faster.",
            },
            {
                "title": "Public/private split",
                "decision": "Separate approved public facts and public snapshots from the private brain.",
                "rationale": "The public clone needed to feel rich without risking leakage from private notes or secrets.",
                "tradeoff": "It adds curation overhead, but that overhead is what makes the system trustworthy.",
            },
            {
                "title": "Async worker architecture",
                "decision": "Keep the bot thin and push heavy work onto Redis-backed workers.",
                "rationale": "That gave me resilience, better cost control, and room for extraction pipelines and retries.",
                "tradeoff": "More moving parts, but far better operational behavior.",
            },
        ],
        "iterations": [
            {
                "title": "TypeScript and WhatsApp origin",
                "summary": "The first version proved the concept, but it was too heavy and awkward for the always-on loop I wanted.",
            },
            {
                "title": "Python and Discord rewrite",
                "summary": "I rebuilt the system around Python async, ARQ, FastAPI, and MCP so it matched the workflow and tooling I actually use.",
            },
            {
                "title": "Story-first public surface",
                "summary": "The latest phase added curated public snapshots, dashboard views, and a public-safe clone instead of exposing raw memory directly.",
            },
        ],
        "struggles": [
            {
                "problem": "Structured classification output was inconsistent early on.",
                "resolution": "I tightened prompts, added confidence-based review thresholds, and treated sub-threshold outputs as reviewable instead of silently trusting them.",
            },
            {
                "problem": "It was easy for public rendering to drift back toward raw dumps.",
                "resolution": "I introduced curated public payloads and snapshot precedence so the site reads designed narratives instead of raw fact bodies.",
            },
            {
                "problem": "The system kept expanding in scope faster than its interfaces stayed legible.",
                "resolution": "I split the product into private brain, curated public surface, dashboard ops, and agent interfaces with different contracts.",
            },
        ],
        "learnings": [
            "Agentic systems live or die on orchestration boundaries, not on the headline model.",
            "A second brain is only useful if capture is frictionless and retrieval is trustworthy.",
            "Public AI experiences need a real data contract; otherwise they drift into generic filler or accidental leakage.",
        ],
        "next_improvements": [
            "Expand daily refreshes into more visible freshness windows and publish provenance more clearly.",
            "Deepen the dashboard review lane so every public rewrite has a tighter before/after diff and evidence mapping.",
            "Keep improving the autonomous campaign layer so the brain can identify and stage product improvements on its own cadence.",
        ],
    },
    "datagenie": {
        "hero_label": "Conversational Analytics",
        "project_framing": (
            "dataGenie is about lowering the skill barrier to data analysis without flattening the work into toy answers. "
            "I wanted a system where a non-technical person could ask a question in plain English and still get something "
            "that feels analytically serious."
        ),
        "problem": (
            "Most internal analytics tools assume one of two extremes: either the user writes SQL or the product gives them "
            "a shallow dashboard that cannot answer new questions. I wanted a middle path: conversational access to real data "
            "with enough structure and guardrails to stay useful."
        ),
        "why_now": (
            "The recent wave of LLM tooling made the interface side easier, but it also made me care more about routing, "
            "fallbacks, and quality boundaries. The interesting work was not 'chat with CSVs'; it was deciding when not to use an agent."
        ),
        "architecture_narrative": (
            "The core architecture is hybrid by design. Uploaded data lands in DuckDB after profiling. A lightweight intent layer "
            "decides whether the request is simple enough for direct SQL or complex enough to go through an agentic reasoning loop. "
            "The LLM layer sits behind a provider abstraction with fallbacks so the product can keep answering even when one provider degrades."
        ),
        "architecture_diagram": {
            "title": "Hybrid Query Routing",
            "caption": "Simple questions should move fast. Complex questions should decompose before they answer.",
            "lanes": [
                {
                    "label": "Input",
                    "nodes": [
                        {"id": "dg_user", "label": "User question", "detail": "plain-English analytics"},
                        {"id": "dg_data", "label": "CSV upload", "detail": "tabular source data"},
                    ],
                },
                {
                    "label": "Preparation",
                    "nodes": [
                        {"id": "dg_profile", "label": "Data profiler", "detail": "schema, nulls, distributions"},
                        {"id": "dg_router", "label": "Intent router", "detail": "simple vs complex path"},
                    ],
                },
                {
                    "label": "Execution",
                    "nodes": [
                        {"id": "dg_sql", "label": "Direct SQL path", "detail": "fast answers in DuckDB"},
                        {"id": "dg_agent", "label": "ReAct loop", "detail": "plan, query, synthesize"},
                        {"id": "dg_llm", "label": "Provider layer", "detail": "Claude, OpenAI, Ollama"},
                    ],
                },
                {
                    "label": "Output",
                    "nodes": [
                        {"id": "dg_answer", "label": "Answer + charts", "detail": "query result, explanation, viz"},
                    ],
                },
            ],
            "edges": [
                {"from": "dg_data", "to": "dg_profile", "label": "profile"},
                {"from": "dg_user", "to": "dg_router", "label": "classify"},
                {"from": "dg_profile", "to": "dg_router", "label": "shape context"},
                {"from": "dg_router", "to": "dg_sql", "label": "simple"},
                {"from": "dg_router", "to": "dg_agent", "label": "complex"},
                {"from": "dg_agent", "to": "dg_llm", "label": "reason"},
                {"from": "dg_sql", "to": "dg_answer", "label": "results"},
                {"from": "dg_agent", "to": "dg_answer", "label": "synthesis"},
            ],
            "callouts": [
                {
                    "label": "Routing principle",
                    "body": "One path for every question either slows simple queries down or underpowers complex ones.",
                },
                {
                    "label": "Provider resilience",
                    "body": "The model layer is abstracted so failures or rate limits do not collapse the product.",
                },
            ],
        },
        "key_decisions": [
            {
                "title": "Hybrid query routing",
                "decision": "Do not force all questions through an agent loop.",
                "rationale": "Simple count, filter, and aggregation queries are faster and more reliable when translated directly.",
                "tradeoff": "The routing layer adds complexity, but it keeps the user experience honest.",
            },
            {
                "title": "DuckDB as the analytics core",
                "decision": "Use DuckDB instead of a transactional database as the primary query engine.",
                "rationale": "The product is fundamentally analytical, so columnar execution and local speed matter more than OLTP patterns.",
                "tradeoff": "It is a narrower fit, but the fit is much better for ad hoc analytics.",
            },
            {
                "title": "Provider abstraction",
                "decision": "Treat LLM vendors as interchangeable infrastructure rather than as the product itself.",
                "rationale": "Reliability and cost matter too much to tie the experience to one provider.",
                "tradeoff": "Slightly more plumbing up front, much better operational control later.",
            },
        ],
        "iterations": [
            {"title": "Plain-English analytics MVP", "summary": "Started from the user problem: helping non-technical people query data without writing SQL."},
            {"title": "Routing refinement", "summary": "The architecture matured when I stopped pretending all questions deserved the same execution path."},
            {"title": "Provider resilience", "summary": "Fallback logic turned the system from a prototype into something that could survive real provider instability."},
        ],
        "struggles": [
            {
                "problem": "Early agent-heavy designs made easy questions feel slower and more fragile than they should have.",
                "resolution": "I split the paths so simple questions can stay direct and fast.",
            },
            {
                "problem": "LLMs answered better once they understood dataset shape, but that context was missing at first.",
                "resolution": "I added profiling ahead of query generation so the model sees schema and quality context before reasoning.",
            },
        ],
        "learnings": [
            "The smartest architecture is often the one that knows when not to invoke heavy reasoning.",
            "Data products need quality context before language interfaces can be trusted.",
            "Provider redundancy is part of product design, not just ops hygiene.",
        ],
        "next_improvements": [
            "Finish the frontend experience so the analysis flow feels as polished as the routing logic underneath it.",
            "Add richer visualization states and conversational follow-up memory.",
            "Tighten evaluation around answer quality, not just query success.",
        ],
    },
    "balkan-barbershop-website": {
        "hero_label": "Full Booking Platform",
        "project_framing": (
            "Balkan started as a design-heavy client website but evolved into a much more serious product: a real booking platform "
            "with operations, payments, reminders, and admin workflows that had to work for an actual shop."
        ),
        "problem": (
            "A barbershop does not just need a pretty homepage. It needs a customer flow that reduces friction for bookings, "
            "supports staff operations, and does not create more work for the owner. The core problem was operational reliability "
            "wrapped in a premium brand presentation."
        ),
        "why_now": (
            "This project forced me to move past portfolio aesthetics and deal with the realities of client software: payment states, "
            "notifications, reschedules, admin visibility, and deployment stability."
        ),
        "architecture_narrative": (
            "The architecture became a classic three-layer product: React frontend for customer and admin surfaces, Node/Express APIs "
            "for booking/payment/notification logic, and PostgreSQL for bookings, services, users, and operational state. Around that "
            "core, I iterated on notifications, Stripe payments, and deployment until the product matched how the shop actually runs."
        ),
        "architecture_diagram": {
            "title": "Booking Flow and Shop Operations",
            "caption": "The experience had to serve both the customer booking path and the owner/admin operating path.",
            "lanes": [
                {
                    "label": "Customer",
                    "nodes": [
                        {"id": "bb_customer", "label": "Public site", "detail": "services, barber, schedule"},
                        {"id": "bb_checkout", "label": "Booking + payment", "detail": "Stripe-backed flow"},
                    ],
                },
                {
                    "label": "Application",
                    "nodes": [
                        {"id": "bb_api", "label": "Node/Express API", "detail": "auth, booking, reminders"},
                        {"id": "bb_notify", "label": "Notification layer", "detail": "email, SMS, reminders"},
                    ],
                },
                {
                    "label": "Operations",
                    "nodes": [
                        {"id": "bb_admin", "label": "Admin dashboard", "detail": "appointments, analytics, staff ops"},
                        {"id": "bb_db", "label": "PostgreSQL", "detail": "bookings, services, users, payments"},
                    ],
                },
                {
                    "label": "Infra",
                    "nodes": [
                        {"id": "bb_deploy", "label": "DigitalOcean deploy", "detail": "Docker + Nginx"},
                    ],
                },
            ],
            "edges": [
                {"from": "bb_customer", "to": "bb_checkout", "label": "select"},
                {"from": "bb_checkout", "to": "bb_api", "label": "submit"},
                {"from": "bb_api", "to": "bb_db", "label": "persist"},
                {"from": "bb_api", "to": "bb_notify", "label": "confirm"},
                {"from": "bb_db", "to": "bb_admin", "label": "surface ops"},
                {"from": "bb_api", "to": "bb_admin", "label": "manage"},
                {"from": "bb_api", "to": "bb_deploy", "label": "ship"},
            ],
            "callouts": [
                {
                    "label": "Real business constraint",
                    "body": "Booking reliability mattered more than novelty because staff and customers depended on it.",
                },
                {
                    "label": "Operational loop",
                    "body": "The admin surface was part of the product, not an afterthought after the marketing site.",
                },
            ],
        },
        "key_decisions": [
            {
                "title": "Cut the AI-first detour",
                "decision": "Move away from early AI-heavy concepts and focus on the core booking product.",
                "rationale": "The business value was in dependable bookings, not novelty features that added maintenance burden.",
                "tradeoff": "Less flashy, far more useful.",
            },
            {
                "title": "Treat payments and reminders as product fundamentals",
                "decision": "Integrate Stripe, reminders, rescheduling, and admin workflows into the core system.",
                "rationale": "That is what turned the work from a brochure site into something operationally meaningful.",
                "tradeoff": "A much larger implementation surface, but also a much stronger proof point.",
            },
            {
                "title": "Simplify infrastructure",
                "decision": "Favor the leaner deployment path that the shop could actually live with.",
                "rationale": "A small business does not need heroically complex infrastructure if the simpler path is more maintainable.",
                "tradeoff": "Less theoretical scalability, much better owner fit.",
            },
        ],
        "iterations": [
            {"title": "AI-heavy prototype", "summary": "Started broader and more experimental than the client actually needed."},
            {"title": "Booking product consolidation", "summary": "The project sharpened once bookings, auth, reminders, and admin tooling became the main arc."},
            {"title": "Hardening and redesign", "summary": "Later work tightened the UI, operations, and deployment until it felt like a real platform."},
        ],
        "struggles": [
            {
                "problem": "The early scope was too broad and made the product harder to stabilize.",
                "resolution": "I cut back to the pieces the business would genuinely use and depend on every week.",
            },
            {
                "problem": "Notification and payment providers shifted over time.",
                "resolution": "I treated those integrations as replaceable operational components instead of as hard-coded assumptions.",
            },
            {
                "problem": "Aesthetic quality could not come at the cost of the booking flow.",
                "resolution": "I let the operational path drive the architecture and layered the brand treatment around it.",
            },
        ],
        "learnings": [
            "Client products get better when the software matches the owner’s real operational rhythm.",
            "A strong aesthetic is most convincing when the plumbing beneath it is equally serious.",
            "The best product decision was subtractive: removing complexity the business did not need.",
        ],
        "next_improvements": [
            "Tighten analytics, rebooking flows, and customer retention features.",
            "Make service and staff operations even easier for a small team to manage without support overhead.",
            "Continue simplifying the operational stack wherever it reduces fragility.",
        ],
    },
    "kaffa-espresso-bar-website": {
        "hero_label": "Small-Business Brand + Deployment",
        "project_framing": (
            "Kaffa is a smaller software system than Balkan, but it is still serious work because it became a real shop’s public front door. "
            "The project combines brand translation, static-site craft, and the operational details needed to make a small-business site feel reliable."
        ),
        "problem": (
            "The café needed a web presence that actually matched the space: clear visual identity, mobile-friendly information, discoverability, "
            "and a deployment path that would not become a maintenance burden."
        ),
        "why_now": (
            "This was a good forcing function for learning how to treat a 'simple site' like production software. Domain migration, HTTPS, SEO, "
            "release safety, and rollback behavior matter even more when the site is small because the margin for operational mess is tiny."
        ),
        "architecture_narrative": (
            "Kaffa is intentionally simple at the application layer: static frontend assets, custom CSS and JavaScript, and a content structure built "
            "for discovery and mobile use. The interesting architecture lives in deployment: a git-driven release workflow on a DigitalOcean droplet, "
            "Nginx routing, Certbot HTTPS, and a safe cutover from legacy domains to the final primary domain."
        ),
        "architecture_diagram": {
            "title": "Static Site with Production Release Flow",
            "caption": "The product is lightweight, but the deployment path is engineered to be safe for a real business.",
            "lanes": [
                {
                    "label": "Build",
                    "nodes": [
                        {"id": "kf_repo", "label": "Git repo", "detail": "HTML, CSS, JS, assets"},
                        {"id": "kf_script", "label": "Deploy scripts", "detail": "bootstrap, release, rollback"},
                    ],
                },
                {
                    "label": "Server",
                    "nodes": [
                        {"id": "kf_release", "label": "Release directories", "detail": "timestamped deploys"},
                        {"id": "kf_symlink", "label": "Atomic symlink cutover", "detail": "instant switch"},
                    ],
                },
                {
                    "label": "Delivery",
                    "nodes": [
                        {"id": "kf_nginx", "label": "Nginx", "detail": "domain routing + redirects"},
                        {"id": "kf_https", "label": "Certbot HTTPS", "detail": "webroot ACME flow"},
                        {"id": "kf_site", "label": "kaffaespressobar.com", "detail": "live business site"},
                    ],
                },
            ],
            "edges": [
                {"from": "kf_repo", "to": "kf_script", "label": "push"},
                {"from": "kf_script", "to": "kf_release", "label": "stage"},
                {"from": "kf_release", "to": "kf_symlink", "label": "promote"},
                {"from": "kf_symlink", "to": "kf_nginx", "label": "serve"},
                {"from": "kf_nginx", "to": "kf_https", "label": "secure"},
                {"from": "kf_https", "to": "kf_site", "label": "deliver"},
            ],
            "callouts": [
                {
                    "label": "Release safety",
                    "body": "Versioned releases and symlink cutovers make rollback fast and low-risk.",
                },
                {
                    "label": "Small-business fit",
                    "body": "The system avoids platform sprawl while still handling domains, HTTPS, and redirects cleanly.",
                },
            ],
        },
        "key_decisions": [
            {
                "title": "Static-first product shape",
                "decision": "Keep the application layer simple and invest effort in brand fidelity and deploy reliability.",
                "rationale": "The business needed clarity, speed, and maintainability more than a CMS or a heavy stack.",
                "tradeoff": "Less dynamic tooling, but a tighter and more dependable end result.",
            },
            {
                "title": "Release-based deployment",
                "decision": "Use release directories and atomic cutover instead of ad hoc file replacement.",
                "rationale": "That made it safer to update a live customer-facing site.",
                "tradeoff": "More scripting up front, much better operational confidence later.",
            },
            {
                "title": "Webroot ACME flow",
                "decision": "Use webroot-based Certbot renewal and domain cutover handling.",
                "rationale": "It reduces downtime risk and plays better with a live site on the server.",
                "tradeoff": "Slightly more setup complexity for a cleaner long-term path.",
            },
        ],
        "iterations": [
            {"title": "Initial static build", "summary": "Started as a clean brand-forward site focused on menu, location, and atmosphere."},
            {"title": "Deployment tooling", "summary": "The product matured when deployment and rollback became first-class parts of the system."},
            {"title": "Primary domain cutover", "summary": "Finishing the migration to the final domain turned it into a proper live client delivery."},
        ],
        "struggles": [
            {
                "problem": "The application itself was simple, but domain, HTTPS, and redirect work were not.",
                "resolution": "I treated deployment as part of the product, scripted it, and designed for rollback.",
            },
            {
                "problem": "A small site can still break a business’s online presence if release handling is sloppy.",
                "resolution": "I used release directories, smoke checks, and explicit cutover logic instead of one-shot deploys.",
            },
        ],
        "learnings": [
            "Small-business web work is product work when the site is the brand’s main digital surface.",
            "Simple stacks still deserve release safety, HTTPS hygiene, and intentional operational design.",
            "A polished static site can be a stronger choice than a more complicated stack when the business need is clear.",
        ],
        "next_improvements": [
            "Keep refining performance, discoverability, and content tooling without bloating the stack.",
            "Package the deploy workflow into a reusable template for future small-business work.",
            "Continue improving visual storytelling while preserving operational simplicity.",
        ],
    },
}


def ordered_public_project_slugs() -> list[str]:
    return list(_PUBLIC_PROJECT_REGISTRY)


def canonical_public_project_slug(value: str | None) -> str:
    slug = _slugify(value)
    if not slug:
        return ""
    for canonical_slug, item in _PUBLIC_PROJECT_REGISTRY.items():
        aliases = {_slugify(alias) for alias in item.get("aliases", [])}
        if slug == canonical_slug or slug in aliases:
            return canonical_slug
    return slug


def _project_registry_entry(value: str | None) -> dict[str, Any]:
    return dict(_PUBLIC_PROJECT_REGISTRY.get(canonical_public_project_slug(value), {}))


def _load_interest_payload(seed_dir: Path) -> dict[str, Any]:
    candidates = [seed_dir / "interests.json"]
    for candidate_root in _public_seed_candidates():
        candidate = candidate_root / "interests.json"
        if candidate not in candidates:
            candidates.append(candidate)
    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            return json.loads(candidate.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
    return {}


def _taste_modules(seed_dir: Path) -> list[dict[str, Any]]:
    payload = _load_interest_payload(seed_dir)
    specs = [
        ("top5_youtubers", "Top YouTubers", "Rabbit holes I keep returning to"),
        ("top5_anime", "Top Anime", "Story structure and taste signals"),
        ("top5_shows", "Top Shows", "Comfort rewatches and precision obsessions"),
        ("top5_artists", "Top Artists", "What is in rotation"),
    ]
    modules: list[dict[str, Any]] = []
    for key, title, eyebrow in specs:
        items = list(payload.get(key) or [])
        if not items:
            continue
        modules.append(
            {
                "slug": _slugify(title),
                "title": title,
                "eyebrow": eyebrow,
                "items": [
                    {
                        "rank": index + 1,
                        "name": _compact(item.get("name")),
                        "subtitle": _compact(item.get("subtitle")),
                        "link": _find_url(item.get("link")),
                    }
                    for index, item in enumerate(items[:5])
                    if _compact(item.get("name"))
                ],
            }
        )
    return modules


def _supporting_evidence_for_project(
    project: ProjectCase,
    repo_history: dict[str, Any],
    *,
    source_refs: list[str],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    if project.summary:
        evidence.append(
            {
                "label": "Curated narrative summary",
                "kind": "narrative",
                "summary": project.summary,
                "source_refs": source_refs[:2],
            }
        )
    if repo_history.get("executive_summary"):
        evidence.append(
            {
                "label": "Repo history executive summary",
                "kind": "repo_history",
                "summary": _excerpt(repo_history.get("executive_summary"), limit=220),
                "source_refs": [f"repo_history:{project.slug}"],
            }
        )
    phases = list(repo_history.get("phases") or [])
    if phases:
        latest_phase = phases[-1]
        evidence.append(
            {
                "label": latest_phase.get("title") or "Latest recorded phase",
                "kind": "phase",
                "summary": _excerpt(latest_phase.get("narrative"), limit=220),
                "source_refs": [f"repo_history:{project.slug}:{_slugify(latest_phase.get('title'))}"],
            }
        )
    tech_stack = list(repo_history.get("tech_stack") or [])
    if tech_stack:
        evidence.append(
            {
                "label": "Technical stack evidence",
                "kind": "stack",
                "summary": ", ".join(_dedupe_strings(tech_stack)[:8]),
                "source_refs": [f"repo_history:{project.slug}:tech-stack"],
            }
        )
    return evidence[:5]


def _fallback_daily_update_window(project: ProjectCase, repo_history: dict[str, Any]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for phase in list(repo_history.get("phases") or [])[-3:]:
        headline = _compact(phase.get("title"))
        summary = _excerpt(phase.get("narrative"), limit=180)
        if not headline or not summary:
            continue
        items.append(
            {
                "headline": headline,
                "summary": summary,
                "timestamp_label": _compact(phase.get("date_range")) or "Historical phase",
                "evidence_refs": [f"repo_history:{project.slug}:{_slugify(headline)}"],
            }
        )
    if not items and project.summary:
        items.append(
            {
                "headline": "Canonical project snapshot",
                "summary": project.summary,
                "timestamp_label": "Curated baseline",
                "evidence_refs": [f"project:{project.slug}:baseline"],
            }
        )
    return {
        "title": "Latest evolution",
        "style": "slider",
        "items": items[:3],
    }


def _curated_case_study_payload(
    project: ProjectCase,
    repo_history: dict[str, Any],
    *,
    source_refs: list[str],
) -> dict[str, Any]:
    override = dict(_FLAGSHIP_CASE_STUDY_OVERRIDES.get(project.slug) or {})
    if not override:
        return {}

    decisions = list(override.get("key_decisions") or [])
    tradeoffs = [
        {
            "title": item.get("title") or item.get("decision") or "",
            "body": item.get("tradeoff") or "",
        }
        for item in decisions
        if _compact(item.get("tradeoff"))
    ]
    supporting_evidence = _supporting_evidence_for_project(
        project,
        repo_history,
        source_refs=source_refs,
    )
    appendix = {
        "metrics": dict(repo_history.get("code_metrics") or {}),
        "timeline_ascii": repo_history.get("timeline_ascii") or "",
        "tech_stack": _dedupe_strings(
            list(project.stack) + list(repo_history.get("tech_stack") or [])
        )[:12],
    }
    return {
        "hero_label": override.get("hero_label") or "Case Study",
        "project_framing": override.get("project_framing") or project.summary,
        "problem": override.get("problem") or project.summary,
        "why_now": override.get("why_now") or "",
        "role_scope": project.role_scope,
        "constraints": list(project.constraints or []),
        "architecture_narrative": override.get("architecture_narrative") or "",
        "architecture_diagram": dict(override.get("architecture_diagram") or {}),
        "key_decisions": decisions,
        "tradeoffs": tradeoffs,
        "iterations": list(override.get("iterations") or []),
        "struggles": list(override.get("struggles") or []),
        "learnings": list(override.get("learnings") or []),
        "outcomes": list(project.outcomes or []),
        "next_improvements": list(override.get("next_improvements") or []),
        "supporting_evidence": supporting_evidence,
        "appendix": appendix,
        "case_study_sections": list(_CASE_STUDY_SECTION_ORDER),
        "last_curated_at": "2026-03-23",
        "curation_mode": "authored_brain_snapshot",
    }


def _dedupe_strings(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _compact(value)
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(cleaned)
    return deduped


def _dedupe_links(links: list[dict[str, str]]) -> list[dict[str, str]]:
    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in links:
        href = str(item.get("href") or "").strip()
        label = str(item.get("label") or "").strip()
        if not href:
            continue
        key = href.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append({"label": label or "Open", "href": href})
    return deduped


def _photo_assets(seed_dir: Path, text: str) -> dict[str, PhotoAsset]:
    assets: dict[str, PhotoAsset] = {}
    for match in re.finditer(
        r"^###\s+(?P<title>.+?)\n(?P<body>.*?)(?=^###\s+|\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    ):
        title = _compact(match.group("title"))
        body = match.group("body")
        fields = _kv_lines(body)
        filename = fields.get("filename", "")
        if not filename or not (seed_dir / "website_photos" / filename).exists():
            continue
        description = fields.get("description", "")
        vibe = fields.get("vibe", "")
        best_for = [
            part.strip() for part in re.split(r",\s*", fields.get("best for", "")) if part.strip()
        ]
        assets[_slugify(title)] = PhotoAsset(
            key=_slugify(title),
            filename=filename,
            title=title,
            description=description,
            vibe=vibe,
            best_for=best_for,
        )
    return assets


def _photo_selection(assets: dict[str, PhotoAsset]) -> dict[str, dict[str, Any] | None]:
    def pick(filename: str) -> dict[str, Any] | None:
        for asset in assets.values():
            if asset.filename == filename:
                return asset.as_dict()
        return None

    return {
        # Original 13 JPGs
        "hero": pick("05_nov2025_waterfront_fullbody_portrait.jpg"),
        "personality": pick("09_aug2025_holding_oscar_colorful_art_wall.jpg"),
        "work": pick("02_feb2026_nyc_street_portrait_with_badge.jpg"),
        "contact": pick("03_jan2026_oscar_on_shoulder_white_wall.jpg"),
        "home": pick("01_feb2026_home_selfie_with_oscar_and_iris.jpg"),
        "wedding": pick("13_nov2025_wedding_love_sign_flower_arch.jpg"),
        "couple": pick("06_nov2025_couple_kiss_waterfront_dramatic_sky.jpg"),
        "pokemon": pick("10_aug2025_pokemon_plushies.jpg"),
        "cycling": pick("07_sep2025_bike_helmet_oscar_front_door.jpg"),
        "photo_break": pick("11_jul2025_couple_sunset_nyc_skyline.jpg"),
        "oscar_looking": pick("04_jan2026_oscar_on_shoulder_looking_away.jpg"),
        "oscar_home": pick("08_aug2025_holding_oscar_at_home.jpg"),
        "skyline_2": pick("12_jul2025_couple_sunset_nyc_skyline_2.jpg"),
        # New 12 PNGs
        "oscar_selfie": pick("ahmad_selfie_with_oscar.png"),
        "indian_wedding": pick("ahmad_indian_wedding_tulips.png"),
        "ghibli_picnic": pick("ahmad_ghibli_picnic.png"),
        "ghibli_cat": pick("ahmad_with_cat_ghibli.png"),
        "baking": pick("baking_hamantaschen.png"),
        "friends_brooklyn": pick("group_photo_brooklyn.png"),
        "oscar_couch": pick("oscar_cat_couch_closeup.png"),
        "oscar_sploot": pick("oscar_cat_from_above.png"),
        "oscar_office": pick("oscar_cat_office_chair.png"),
        "oscar_chair": pick("oscar_cat_on_chair.png"),
        "oscar_stairs": pick("oscar_cat_stairs.png"),
        "oscar_window": pick("oscar_cat_window.png"),
        # Collections
        "mosaic": [
            pick("09_aug2025_holding_oscar_colorful_art_wall.jpg"),
            pick("13_nov2025_wedding_love_sign_flower_arch.jpg"),
            pick("07_sep2025_bike_helmet_oscar_front_door.jpg"),
            pick("10_aug2025_pokemon_plushies.jpg"),
            pick("06_nov2025_couple_kiss_waterfront_dramatic_sky.jpg"),
            pick("11_jul2025_couple_sunset_nyc_skyline.jpg"),
        ],
        "gallery": [
            asset.as_dict()
            for asset in sorted(assets.values(), key=lambda item: item.filename)
        ],
    }


# Fallback links/stack for known projects (URLs not always in seed text)
_KNOWN_PROJECT_EXTRAS: dict[str, dict] = {
    "balkan-barbershop-website": {
        "links": [{"label": "Live site", "href": "https://balkan.thisisrikisart.com"}],
        "stack": [
            "React",
            "Node.js/Express",
            "PostgreSQL",
            "Stripe",
            "Resend",
            "Twilio",
            "Docker",
            "DigitalOcean",
        ],
    },
    "kaffa-espresso-bar-website": {
        "links": [{"label": "Live site", "href": "https://kaffaespressobar.com"}],
        "stack": [
            "HTML",
            "CSS",
            "JavaScript",
            "Nginx",
            "Certbot",
            "Bash",
            "DigitalOcean",
        ],
    },
    "dusrabheja": {
        "links": [{"label": "GitHub", "href": "https://github.com/AhmadSK95/duSraBheja"}],
        "stack": [
            "Python",
            "FastAPI",
            "PostgreSQL",
            "pgvector",
            "ARQ",
            "Discord.py",
            "Docker",
        ],
    },
    "datagenie": {
        "links": [{"label": "GitHub", "href": "https://github.com/AhmadSK95/dataGenie"}],
        "stack": ["Python", "FastAPI", "DuckDB", "Redis", "Celery", "Docker"],
    },
    "teachassist-ai": {
        "stack": ["TypeScript", "Next.js", "Python", "Claude API", "Docker", "Playwright"],
    },
    "ai-resume-matcher": {
        "stack": ["Python", "Flask", "React", "ChromaDB", "OpenAI", "Docker"],
    },
}


_PHOTO_CAPTIONS: dict[str, str] = {
    "wedding": "November 2025. Courthouse. The LOVE sign was her idea.",
    "pokemon": "The OG starters. Non-negotiable.",
    "cycling": "Oscar waits at the door every time.",
    "personality": "The shirt says 繰り返す. Repeat.",
    "home": "The workspace. Two monitors, two cats, one rug.",
    "couple": "Jersey City waterfront. Dramatic sky optional.",
    "hero": "Jersey City. North Face. Beanie season.",
    "oscar_looking": "Oscar judging my architecture decisions.",
    "oscar_home": "The real boss of the house.",
    "skyline_2": "Jersey City skyline. Golden hour.",
}


def _clean_demonstrates(items: list[str]) -> list[str]:
    """Filter garbage entries from demonstrates lists."""
    return [
        item
        for item in items
        if item and item.strip() not in {"---", "--", "-", ""} and len(item.strip()) >= 10
    ]


def _parse_project_descriptions(text: str) -> tuple[str, list[ProjectCase]]:
    sections = list(
        re.finditer(
            r"^###\s+(?P<title>.+?)\n(?P<body>.*?)(?=^###\s+|\Z)",
            text,
            re.MULTILINE | re.DOTALL,
        )
    )
    summary_match = re.search(
        r"##\s+PROFESSIONAL SUMMARY.*?\n\n(?P<body>.*?)(?=\n##\s+KEY PROJECTS|\Z)",
        text,
        re.DOTALL,
    )
    professional_summary = _excerpt(
        re.sub(
            r"^\s*---\s*$",
            "",
            summary_match.group("body") if summary_match else "",
            flags=re.MULTILINE,
        ),
        limit=1000,
    )
    projects: list[ProjectCase] = []
    for match in sections:
        title = _compact(match.group("title"))
        body = match.group("body")
        slug = canonical_public_project_slug(title.split(" - ", 1)[0])
        resume_body = _extract_labeled_block(
            body,
            "Resume",
            stop_labels=["LinkedIn", "What this project demonstrates"],
        )
        linkedin_body = _extract_labeled_block(
            body,
            "LinkedIn",
            stop_labels=["What this project demonstrates"],
        )
        demonstrates_body = _extract_labeled_block(
            body,
            "What this project demonstrates",
        )
        resume_bullets = _bullet_lines(resume_body)
        demonstrates = _clean_demonstrates(_bullet_lines(demonstrates_body))
        full_body = _compact(linkedin_body or resume_body or body)
        stack: list[str] = []
        stack_match = re.search(r"Stack:\s*(?P<value>.+?)(?:\.|$)", body)
        if stack_match:
            stack = [item.strip() for item in stack_match.group("value").split(",") if item.strip()]
        status = "Live client project" if "live client project" in title.lower() else "Active build"
        links: list[dict[str, str]] = []
        live_url = _find_url(body)
        if live_url:
            links.append({"label": "Live site", "href": live_url})

        # Merge known fallback links and stack
        extras = _KNOWN_PROJECT_EXTRAS.get(slug, {})
        existing_hrefs = {lk.get("href") for lk in links}
        for fl in extras.get("links", []):
            if fl["href"] not in existing_hrefs:
                links.append(fl)
        if extras.get("stack"):
            stack = _dedupe_strings(list(stack) + list(extras["stack"]))

        projects.append(
            ProjectCase(
                slug=slug,
                title=title,
                tagline=(resume_bullets[0] if resume_bullets else _excerpt(full_body, limit=180)),
                summary=_excerpt(full_body, limit=340),
                status=status,
                stack=stack,
                resume_bullets=resume_bullets,
                body=full_body,
                demonstrates=demonstrates,
                links=_dedupe_links(links),
                proof=resume_bullets[:2] + demonstrates[:2],
                case_study_sections=list(_CASE_STUDY_SECTION_ORDER),
            )
        )
    return professional_summary, projects


def _extract_other_project_summary(text: str, label: str) -> str:
    pattern = re.compile(
        rf"\*\*{re.escape(label)}[^*]*\*\*:\s*(?P<body>.*?)(?=\n\n\*\*|\Z)",
        re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        return ""
    return _compact(match.group("body"))


def _repo_history_for_slug(
    repo_histories: dict[str, dict[str, Any]], slug: str
) -> dict[str, Any]:
    canonical_slug = canonical_public_project_slug(slug)
    for key, payload in repo_histories.items():
        if canonical_public_project_slug(key) == canonical_slug:
            return dict(payload)
    return {}


def _secondary_project_cases(
    personal_bible_text: str,
    repo_histories: dict[str, dict[str, Any]],
) -> list[ProjectCase]:
    secondary_specs = [
        (
            "teachassist-ai",
            _extract_other_project_summary(personal_bible_text, "TeachAssist AI"),
            [
                "Monorepo architecture with educator-facing generation workflows.",
                "Compliance-aware design for classroom contexts and differentiated materials.",
            ],
        ),
        (
            "ai-resume-matcher",
            _extract_other_project_summary(personal_bible_text, "AI Resume Matcher"),
            [
                "Grounded candidate search across a large resume corpus.",
                "Natural-language querying plus JD analysis and export workflows.",
            ],
        ),
    ]
    projects: list[ProjectCase] = []
    for slug, bible_summary, proof in secondary_specs:
        registry = _project_registry_entry(slug)
        repo_history = _repo_history_for_slug(repo_histories, slug)
        summary = _excerpt(
            repo_history.get("executive_summary") or bible_summary or registry.get("title") or slug,
            limit=340,
        )
        stack = _dedupe_strings(
            list(repo_history.get("tech_stack") or [])
            + list((_KNOWN_PROJECT_EXTRAS.get(slug) or {}).get("stack") or [])
        )
        projects.append(
            ProjectCase(
                slug=slug,
                title=str(registry.get("title") or slug),
                tagline=summary,
                summary=summary,
                status="Secondary proof",
                tier="secondary",
                stack=stack,
                resume_bullets=[],
                body=summary,
                demonstrates=[],
                links=_dedupe_links(list((_KNOWN_PROJECT_EXTRAS.get(slug) or {}).get("links") or [])),
                proof=proof,
                role_scope=str(registry.get("role_scope") or ""),
                constraints=list(registry.get("constraints") or []),
                outcomes=list(registry.get("outcomes") or []),
                case_study_sections=list(_CASE_STUDY_SECTION_ORDER),
                demo_asset=str(registry.get("demo_asset") or ""),
                display_order=int(registry.get("order") or 999),
            )
        )
    return projects


def _curate_public_projects(
    projects: list[ProjectCase],
    *,
    personal_bible_text: str,
    repo_histories: dict[str, dict[str, Any]],
) -> list[ProjectCase]:
    indexed: dict[str, ProjectCase] = {}
    for project in projects + _secondary_project_cases(personal_bible_text, repo_histories):
        slug = canonical_public_project_slug(project.slug)
        registry = _project_registry_entry(slug)
        repo_history = _repo_history_for_slug(repo_histories, slug)
        extras = _KNOWN_PROJECT_EXTRAS.get(slug) or {}
        source_refs = [f"project:{slug}:narrative"]
        if repo_history:
            source_refs.append(f"repo_history:{slug}")
        curated_case_study = _curated_case_study_payload(
            project,
            repo_history,
            source_refs=source_refs,
        )
        daily_update_window = _fallback_daily_update_window(project, repo_history)
        supporting_evidence = list(curated_case_study.get("supporting_evidence") or [])
        indexed[slug] = ProjectCase(
            slug=slug,
            title=str(registry.get("title") or project.title),
            tagline=project.tagline,
            summary=project.summary,
            status=project.status,
            tier=str(registry.get("tier") or project.tier or "flagship"),
            stack=_dedupe_strings(
                list(project.stack)
                + list(repo_history.get("tech_stack") or [])
                + list(extras.get("stack") or [])
            ),
            resume_bullets=list(project.resume_bullets),
            body=project.body,
            demonstrates=_clean_demonstrates(project.demonstrates),
            links=_dedupe_links(list(project.links) + list(extras.get("links") or [])),
            proof=_dedupe_strings(
                list(project.proof)
                + list(project.resume_bullets[:2])
                + list(repo_history.get("tech_stack") or [])[:2]
            )[:5],
            role_scope=str(registry.get("role_scope") or project.role_scope),
            constraints=list(registry.get("constraints") or project.constraints),
            outcomes=list(registry.get("outcomes") or project.outcomes),
            case_study_sections=list(
                project.case_study_sections or registry.get("case_study_sections") or _CASE_STUDY_SECTION_ORDER
            ),
            demo_asset=str(registry.get("demo_asset") or project.demo_asset),
            display_order=(
                int(registry["order"])
                if registry.get("order") is not None
                else int(project.display_order)
            ),
            curated_case_study=curated_case_study,
            daily_update_window=daily_update_window,
            supporting_evidence=supporting_evidence,
            latest_work_summary=(
                _excerpt(
                    (
                        (daily_update_window.get("items") or [{}])[0].get("summary")
                        if daily_update_window.get("items")
                        else ""
                    )
                    or project.summary,
                    limit=200,
                )
            ),
        )
    curated: list[ProjectCase] = []
    for slug in ordered_public_project_slugs():
        if slug in indexed:
            curated.append(indexed[slug])
    return curated


def _parse_roles(job_hunt_text: str) -> list[RoleExperience]:
    sections = _section_map(job_hunt_text)
    roles: list[RoleExperience] = []
    current_title = ""
    current_body: list[str] = []
    for raw in (sections.get("professional background") or "").splitlines():
        stripped = raw.strip()
        if re.match(r"^\*\*.+\*\*\s*\(.+\)$", stripped):
            if current_title:
                roles.append(_role_from_block(current_title, current_body))
            current_title = stripped
            current_body = []
            continue
        current_body.append(raw)
    if current_title:
        roles.append(_role_from_block(current_title, current_body))
    return [role for role in roles if role.title]


def _role_from_block(title_line: str, body_lines: list[str]) -> RoleExperience:
    normalized = _strip_markdown(title_line)
    match = re.match(r"(?P<organization>.+?)\s*-\s*(?P<title>.+?)\s*\((?P<meta>.+?)\)", normalized)
    if match:
        organization = match.group("organization")
        title = match.group("title")
        meta = match.group("meta")
    else:
        organization = normalized
        title = normalized
        meta = ""
    parts = [part.strip() for part in meta.split(",") if part.strip()]
    period = parts[0] if parts else meta
    location = parts[1] if len(parts) > 1 else ""
    bullets = _bullet_lines("\n".join(body_lines))
    return RoleExperience(
        slug=_slugify(f"{organization}-{title}"),
        title=title,
        organization=organization,
        period=period,
        location=location,
        summary=_excerpt(" ".join(bullets), limit=220),
        bullets=bullets,
    )


def _parse_education(job_hunt_text: str) -> list[dict[str, Any]]:
    sections = _section_map(job_hunt_text)
    entries: list[dict[str, Any]] = []
    for bullet in _bullet_lines(sections.get("education")):
        match = re.match(
            r"(?P<degree>.+?)\s*-\s*(?P<school>.+?)\s*\((?P<years>[^)]+)\)\.\s*(?P<details>.+)$",
            bullet,
        )
        if not match:
            continue
        entries.append(
            {
                "school": _compact(match.group("school")),
                "degree": _compact(match.group("degree")),
                "years": _compact(match.group("years")),
                "details": _compact(match.group("details")),
            }
        )
    return entries


def _parse_skill_sections(job_hunt_text: str) -> list[dict[str, Any]]:
    sections = _section_map(job_hunt_text)
    skills: list[dict[str, Any]] = []
    for bullet in _bullet_lines(sections.get("technical skills")):
        match = re.match(r"(?P<label>.+?):\s*(?P<items>.+)$", bullet)
        if not match:
            continue
        items = _dedupe_strings(
            [item.strip() for item in re.split(r",\s*", match.group("items")) if item.strip()]
        )
        skills.append(
            {
                "category": _compact(match.group("label")),
                "items": items,
            }
        )
    return skills


def _resume_sections(
    professional_summary: str,
    roles: list[RoleExperience],
    education: list[dict[str, Any]],
    skills: list[dict[str, Any]],
    projects: list[ProjectCase],
) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    if professional_summary:
        sections.append(
            {
                "slug": "summary",
                "title": "Professional Summary",
                "summary": professional_summary,
                "items": [],
            }
        )
    sections.append(
        {
            "slug": "experience",
            "title": "Experience",
            "summary": "Roles across Amazon, Loylty Rewardz, and early systems work before the builder phase.",
            "items": [
                {
                    "title": role.title,
                    "subtitle": role.organization,
                    "meta": role.period,
                    "details": role.summary,
                }
                for role in roles
            ],
        }
    )
    sections.append(
        {
            "slug": "education",
            "title": "Education",
            "summary": "Formal training that sits underneath the systems and product work.",
            "items": [
                {
                    "title": item.get("degree"),
                    "subtitle": item.get("school"),
                    "meta": item.get("years"),
                    "details": item.get("details"),
                }
                for item in education
            ],
        }
    )
    sections.append(
        {
            "slug": "skills",
            "title": "Technical Skills",
            "summary": "The stack is broad because the work has been end to end.",
            "items": [
                {
                    "title": item.get("category"),
                    "subtitle": "",
                    "meta": "",
                    "details": ", ".join(item.get("items") or []),
                }
                for item in skills
            ],
        }
    )
    sections.append(
        {
            "slug": "projects",
            "title": "Projects",
            "summary": "The strongest current proof sits in the products and client work.",
            "items": [
                {
                    "title": project.title,
                    "subtitle": project.status,
                    "meta": project.tier,
                    "details": project.summary,
                }
                for project in projects
            ],
        }
    )
    return sections


def _parse_capabilities(personal_bible_text: str) -> list[CapabilityBook]:
    technical = _section_body(personal_bible_text, "Part 4: Technical DNA")
    capability_sections = _extract_markdown_sections(technical)
    books: list[CapabilityBook] = []
    for _level, title, body in capability_sections:
        if title.lower() == "document":
            continue
        bullets = _bullet_lines(body)
        if not bullets:
            continue
        books.append(
            CapabilityBook(
                slug=_slugify(title),
                title=title,
                summary=_excerpt(body, limit=220),
                chapters=bullets[:6],
                evidence=bullets[:3],
            )
        )
    if not books:
        books.append(
            CapabilityBook(
                slug="engineering",
                title="Engineering",
                summary="Distributed systems, AI-native backend work, and end-to-end product ownership.",
                chapters=["Backend systems", "AI orchestration", "Deployment and operations"],
                evidence=[],
            )
        )
    return books


def _timeline_from_personal_bible(personal_bible_text: str) -> list[dict[str, str]]:
    rows = _table_rows(_section_map(personal_bible_text).get("part 7: life timeline"))
    timeline: list[dict[str, str]] = []
    for row in rows[1:]:
        if len(row) < 2:
            continue
        timeline.append({"year": row[0], "event": row[1]})
    return timeline


def _eras_from_personal_bible(personal_bible_text: str) -> list[LifeEra]:
    sections = _section_map(personal_bible_text)
    return [
        LifeEra(
            slug="iit-kharagpur",
            title="IIT Kharagpur",
            years="2013-2017",
            summary=_excerpt(
                sections.get("iit kharagpur — b.tech, electrical engineering (2013–2017)"),
                limit=340,
            ),
            highlights=_bullet_lines(
                sections.get("iit kharagpur — b.tech, electrical engineering (2013–2017)")
            )[:5],
            institutions=["IIT Kharagpur"],
            roles=["Student", "Electrical Engineering"],
        ),
        LifeEra(
            slug="mumbai-systems",
            title="Mumbai Systems Years",
            years="2016-2020",
            summary=_excerpt(
                " ".join(
                    [
                        sections.get("citicorp services — summer intern (summer 2016, pune)", ""),
                        sections.get(
                            "loylty rewardz — management trainee → software engineer (july 2017 – april 2020, mumbai)",
                            "",
                        ),
                    ]
                ),
                limit=340,
            ),
            highlights=_bullet_lines(
                sections.get(
                    "loylty rewardz — management trainee → software engineer (july 2017 – april 2020, mumbai)"
                )
            )[:5],
            institutions=["Citicorp Services", "Loylty Rewardz"],
            roles=["Intern", "Management Trainee", "Software Engineer"],
        ),
        LifeEra(
            slug="nyu",
            title="NYU Tandon",
            years="2021-2022",
            summary=_excerpt(
                sections.get(
                    "nyu tandon school of engineering — m.s., electrical engineering (2021–2022)"
                ),
                limit=340,
            ),
            highlights=_bullet_lines(
                sections.get(
                    "nyu tandon school of engineering — m.s., electrical engineering (2021–2022)"
                )
            )[:5],
            institutions=["NYU Tandon"],
            roles=["Graduate Student"],
        ),
        LifeEra(
            slug="amazon",
            title="Amazon",
            years="2022-2025",
            summary=_excerpt(
                sections.get(
                    "amazon — software development engineer (june 2022 – september 2025, nyc)"
                ),
                limit=340,
            ),
            highlights=_bullet_lines(
                sections.get(
                    "amazon — software development engineer (june 2022 – september 2025, nyc)"
                )
            )[:5],
            institutions=["Amazon"],
            roles=["Software Development Engineer"],
        ),
        LifeEra(
            slug="builder-phase",
            title="Independent Builder Phase",
            years="2025-Present",
            summary=_excerpt(
                sections.get("part 3: the builder phase (sep 2025 – present)"), limit=340
            ),
            highlights=[
                "Building AI-native products with real operational surfaces.",
                "Shipping live freelance client sites and infrastructure.",
                "Reframing career story around ownership, product, and systems depth.",
            ],
            institutions=["duSraBheja", "dataGenie", "Balkan", "Kaffa"],
            roles=["Founder-builder", "Freelance engineer"],
        ),
    ]


def _identity_stack(job_hunt_text: str, professional_summary: str) -> list[str]:
    sections = _section_map(job_hunt_text)
    skills = _bullet_lines(sections.get("technical skills"))
    stack = [
        "Software engineer with 6+ years across enterprise, Amazon-scale, and independent product work.",
        "Distributed systems builder working across Java, Python, data systems, and cloud operations.",
        "AI-native product builder focused on memory, analytics, and agent orchestration.",
        "End-to-end owner who designs, ships, deploys, and maintains real products.",
    ]
    if professional_summary:
        stack[0] = professional_summary
    if skills:
        stack.append(_excerpt("Core stack: " + "; ".join(skills[:4]), limit=220))
    return stack[:5]


def _current_arc(
    personal_bible_text: str, brain_dump_text: str, projects: list[ProjectCase]
) -> dict[str, Any]:
    bible_sections = _section_map(personal_bible_text)
    dump_sections = _section_map(brain_dump_text)

    # Parse Part 8 into structured acts instead of raw dump
    part8_body = bible_sections.get("part 8: the narrative arc (for the website)") or ""
    acts: list[dict[str, str]] = []
    act_pattern = re.compile(
        r"\*\*Act\s*\d+\s*[-—–]\s*(?P<label>[^(]+?)\s*\((?P<period>[^)]+)\)\s*:?\s*\*\*"
        r"\s*(?P<body>.*?)(?=\*\*Act\s*\d+|\*\*(?:The\s+)?Throughline|$)",
        re.DOTALL | re.IGNORECASE,
    )
    for m in act_pattern.finditer(part8_body):
        acts.append(
            {
                "label": _compact(m.group("label")),
                "period": _compact(m.group("period")),
                "body": _compact(m.group("body")),
            }
        )

    throughline_match = re.search(
        r"\*\*(?:The\s+)?Throughline\s*:?\s*\*\*\s*(?P<body>.*?)$",
        part8_body,
        re.DOTALL | re.IGNORECASE,
    )
    throughline = _compact(throughline_match.group("body")) if throughline_match else ""

    # Use Act 3 body as summary, or fall back to dump/excerpt
    act3_body = next((a["body"] for a in acts if "builder" in a.get("label", "").lower()), "")
    summary = act3_body or _excerpt(
        dump_sections.get("why i want to join narrative") or part8_body,
        limit=420,
    )

    return {
        "title": "Current Arc",
        "summary": summary,
        "acts": acts,
        "throughline": throughline,
        "focus": [
            "Building duSraBheja into a trustworthy memory and project-state system.",
            "Turning dataGenie into a serious analytics product for non-technical users.",
            "Looking for work where technical depth and product conviction actually align.",
        ],
        "flagship_projects": [project.slug for project in projects[:3]],
    }


def _proof_points(personal_bible_text: str) -> list[dict[str, str]]:
    website_signals = _extract_markdown_sections(
        _section_body(personal_bible_text, "Part 9: Website Content Signals")
    )
    proofs: list[dict[str, str]] = []
    for _level, title, body in website_signals:
        bullets = _bullet_lines(body)
        if not bullets:
            continue
        proofs.append(
            {
                "title": title,
                "summary": _excerpt(" ".join(bullets), limit=220),
                "points": bullets[:5],
            }
        )
    return proofs


def _contact_modes(personal_bible_text: str) -> list[ContactMode]:
    person = _kv_lines(_section_map(personal_bible_text).get("the person"))
    entries = [
        ContactMode(
            key="email",
            label="Email",
            value=settings.public_contact_email or person.get("email", ""),
            href=f"mailto:{settings.public_contact_email or person.get('email', '')}",
            note="Best for serious collaboration, hiring, or direct follow-up.",
        ),
        ContactMode(
            key="linkedin",
            label="LinkedIn",
            value=(settings.public_contact_linkedin_url or person.get("linkedin", "")).replace(
                "https://", ""
            ),
            href=_normalize_public_href(
                settings.public_contact_linkedin_url or person.get("linkedin", "")
            ),
            note="Professional context, work history, and lightweight outreach.",
        ),
        ContactMode(
            key="github",
            label="GitHub",
            value=person.get("github", ""),
            href=_normalize_public_href(person.get("github", "")),
            note="Code, repos, and public engineering output.",
        ),
        ContactMode(
            key="instagram",
            label="Instagram",
            value=(settings.public_contact_instagram_url or person.get("instagram", "")).replace(
                "https://", ""
            ),
            href=_normalize_public_href(
                settings.public_contact_instagram_url or person.get("instagram", "")
            ),
            note="Human context, life moments, and Oscar content.",
        ),
    ]
    return [entry for entry in entries if entry.value and entry.href]


def _personal_texture(personal_bible_text: str) -> list[str]:
    sections = _section_map(personal_bible_text)
    interests = _bullet_lines(sections.get("interests & passions"))
    personal = _split_paragraphs(sections.get("part 5.5: the personal life"))
    texture = [
        "Five languages: English, Hindi, Telugu, Urdu, Tamil.",
        "Married Annie in 2025 — courthouse ceremony, LOVE sign, autumn leaves.",
        "Cat dad to Oscar (7-year orange tabby) and Iris.",
        "Anime watcher — currently on Naruto Shippuden S9, binged My Hero Academia.",
        "Indian standup addict — KVizzing (Members-only), Tanmay Bhat, Rahul Subramanian.",
        "Hip hop, Indian film music, and Def Jam India on repeat.",
        "Cycles around Jersey City, collects Pokemon plushies (the OG starters).",
        "Japanese markets, tattoos, and strong opinions about food.",
    ]
    texture.extend(interests[:2])
    texture.extend(personal[:1])
    deduped: list[str] = []
    seen: set[str] = set()
    for item in texture:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped[:10]


def _personal_signals(
    person: dict[str, str],
    personal_texture: list[str],
    currently: dict[str, Any],
) -> dict[str, Any]:
    languages = [
        item.strip()
        for item in re.split(r",\s*", person.get("languages spoken", ""))
        if item.strip()
    ]
    family = [
        "Annie",
        "Oscar",
        "Iris",
    ]
    cultural_signals = _dedupe_strings(
        [
            "Jersey City life",
            "Anime",
            "Indian standup",
            "Hip hop",
            "Indian film music",
            "Pokemon collecting",
            "Cycling",
        ]
        + personal_texture
    )
    return {
        "home_base": person.get("current base") or settings.public_profile_location,
        "languages": languages,
        "family": family,
        "cultural_signals": cultural_signals[:10],
        "currently": currently,
    }


def _photo_slots(photos: dict[str, Any]) -> list[dict[str, str]]:
    ordered_slots = [
        ("home", "hero", "hero"),
        ("home", "builder-summary", "oscar_selfie"),
        ("home", "open-brain", "ghibli_cat"),
        ("home", "demo-proof", "personality"),
        ("home", "currently", "friends_brooklyn"),
        ("about", "intro", "indian_wedding"),
        ("about", "act-one", "home"),
        ("about", "experience", "work"),
        ("about", "education", "ghibli_picnic"),
        ("about", "skills", "pokemon"),
        ("about", "life", "wedding"),
        ("about", "life", "couple"),
        ("about", "life", "cycling"),
        ("about", "life", "photo_break"),
        ("about", "life", "oscar_looking"),
        ("about", "life", "oscar_home"),
        ("about", "life", "skyline_2"),
        ("about", "life", "baking"),
        ("about", "life", "oscar_couch"),
        ("about", "life", "oscar_sploot"),
        ("about", "life", "oscar_office"),
        ("about", "life", "oscar_chair"),
        ("about", "life", "oscar_stairs"),
        ("about", "life", "oscar_window"),
        ("about", "life", "ghibli_cat"),
    ]
    slots: list[dict[str, str]] = []
    for page, section, key in ordered_slots:
        photo = photos.get(key)
        if not photo:
            continue
        slots.append(
            {
                "page": page,
                "section": section,
                "photo_key": key,
                "filename": str(photo.get("filename") or ""),
                "title": str(photo.get("title") or ""),
            }
        )
    return slots


def _currently_feed(seed_dir: Path) -> dict[str, Any]:
    """Parse Ahmad_Profile_Signal.md for the 'Currently' living feed."""
    signal_path = seed_dir / "Ahmad_Profile_Signal.md"
    text = _read_text(signal_path)
    if not text:
        return {}

    # Extract latest anime from Crunchyroll section
    watching = "Naruto Shippuden"
    watching_detail = "S9 E188"
    crunchyroll_section = _section_body(text, "CRUNCHYROLL ANIME HISTORY")
    if crunchyroll_section:
        for line in crunchyroll_section.splitlines():
            m = re.match(r"\|\s*(.+?)\s+S(\d+)\s+E(\d+).*?\|.*?Watched", line)
            if m:
                watching = m.group(1).strip()
                watching_detail = f"S{m.group(2)} E{m.group(3)}"
                break

    # Extract music from YouTube section
    listening = "Chaar Diwaari"
    listening_detail = "Parvana EP · Def Jam"
    for line in text.splitlines():
        if "Def Jam" in line or "Parvana" in line:
            listening = "Chaar Diwaari"
            listening_detail = "Parvana EP · Def Jam India"
            break

    # Laughing at — KVizzing
    laughing_at = "KVizzing SF4"
    laughing_detail = "Members-only"

    # Stress watch from Netflix section
    stress_watch = "Brooklyn Nine-Nine"
    stress_detail = "S8"
    netflix_section = _section_body(text, "NETFLIX VIEWING HISTORY")
    if netflix_section:
        for line in netflix_section.splitlines():
            m = re.match(r".*Brooklyn Nine-Nine.*Season\s*(\d+)", line)
            if m:
                stress_watch = "Brooklyn Nine-Nine"
                stress_detail = f"S{m.group(1)}"
                break

    # Life moments from pattern analysis
    life_moments = []
    events_section = _section_body(text, "Life Events Reflected in Search")
    if events_section:
        for line in events_section.splitlines():
            m = re.match(r"[-*]\s*\*\*(.+?)\*\*:\s*(.+)", line.strip())
            if m:
                life_moments.append({"date": m.group(1).strip(), "event": _compact(m.group(2))})

    return {
        "watching": watching,
        "watching_detail": watching_detail,
        "listening": listening,
        "listening_detail": listening_detail,
        "laughing_at": laughing_at,
        "laughing_detail": laughing_detail,
        "stress_watch": stress_watch,
        "stress_detail": stress_detail,
        "life_moments": life_moments[:6],
    }


def _parse_repo_histories(seed_dir: Path) -> dict[str, dict[str, Any]]:
    """Parse repo_history_*.md files into structured case study data."""
    histories: dict[str, dict[str, Any]] = {}

    # Slug mapping: filename stem → project slug
    slug_map = {
        "repo_history_duSraBheja": "dusrabheja",
        "repo_history_barbershop": "balkan-barbershop-website",
    }

    for path in sorted(seed_dir.glob("repo_history_*.md")):
        text = _read_text(path)
        if not text:
            continue
        stem = path.stem

        if stem == "repo_history_other_projects":
            # Multi-project file: split on ## N. headings
            project_blocks = re.split(r"\n##\s+\d+\.\s+", text)
            for block in project_blocks[1:]:
                title_line = block.split("\n", 1)[0].strip()
                project_slug = canonical_public_project_slug(
                    title_line.split("(")[0].split("—")[0].strip()
                )
                parsed = _parse_single_repo_history(block, title_line)
                if parsed:
                    histories[project_slug] = parsed
        else:
            project_slug = canonical_public_project_slug(
                slug_map.get(stem, stem.replace("repo_history_", ""))
            )
            parsed = _parse_single_repo_history(text, stem)
            if parsed:
                histories[project_slug] = parsed

    return histories


def _parse_single_repo_history(text: str, fallback_title: str) -> dict[str, Any] | None:
    """Parse a single repo history block into structured data."""
    sections = _section_map(text)
    header_match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    header_title = _compact(header_match.group(1)) if header_match else _compact(fallback_title)
    meta = _kv_lines(text[:2400])
    project_title = (
        _compact(str(meta.get("project") or "").split("—")[0])
        or _compact(fallback_title.split("—")[0])
        or header_title
    )

    # Executive summary
    exec_summary = ""
    exec_summary = _compact(
        _find_section_text(sections, "executive summary", "project overview", "overview")
    )
    if not exec_summary:
        # Try first paragraph of text
        paragraphs = _split_paragraphs(text[:2000])
        if paragraphs:
            exec_summary = paragraphs[0]

    # Phases
    phases: list[dict[str, Any]] = []
    phase_pattern = re.compile(
        r"###\s+Phase\s+\d+[:.]\s*(?P<title>.+?)(?:\s*\((?P<dates>[^)]+)\))?\s*\n(?P<body>.*?)(?=###\s+Phase\s+\d+|##\s+|$)",
        re.DOTALL | re.IGNORECASE,
    )
    for m in phase_pattern.finditer(text):
        title = _compact(m.group("title"))
        dates = _compact(m.group("dates") or "")
        body = m.group("body")
        theme_match = re.search(r"\*\*Theme[:/]?\*\*\s*(.+?)(?:\n|$)", body)
        theme = _compact(theme_match.group(1)) if theme_match else ""
        narrative_parts = _split_paragraphs(body[:1500])
        narrative = " ".join(narrative_parts[:3]) if narrative_parts else ""
        components = _bullet_lines(body)[:6]
        pivot_match = re.search(r"\*\*Pivot[:/]?\*\*\s*(.+?)(?:\n\n|$)", body, re.DOTALL)
        pivot = _compact(pivot_match.group(1)) if pivot_match else ""
        phases.append(
            {
                "title": title,
                "date_range": dates,
                "theme": theme,
                "narrative": narrative,
                "key_components": components,
                "pivot": pivot,
            }
        )

    # Architecture diagrams
    architecture_diagrams: list[dict[str, str]] = []
    arch_pattern = re.compile(
        r"###\s+(?P<title>.*?[Aa]rchitect.*?)\s*\n(?P<body>.*?)(?=###\s+|##\s+|$)",
        re.DOTALL,
    )
    for m in arch_pattern.finditer(text):
        title = _compact(m.group("title"))
        body = m.group("body")
        diagram_match = re.search(r"```[^\n]*\n(.*?)```", body, re.DOTALL)
        diagram = diagram_match.group(1).strip() if diagram_match else ""
        explanation = _compact(re.sub(r"```[^\n]*\n.*?```", "", body, flags=re.DOTALL))
        if diagram or explanation:
            architecture_diagrams.append(
                {
                    "title": title,
                    "diagram": diagram,
                    "explanation": explanation,
                }
            )

    # Architectural decisions
    architectural_decisions: list[dict[str, str]] = []
    decisions_body = _find_section_text(
        sections,
        "architectural decisions",
        "key architectural decisions",
        "key decisions",
        "decisions",
    )
    if decisions_body:
        for item in re.finditer(
            r"\*\*(?P<title>.+?)\*\*[:\s]*(?P<body>.*?)(?=\*\*[A-Z]|\Z)",
            decisions_body,
            re.DOTALL,
        ):
            title = _compact(item.group("title"))
            body = _compact(item.group("body"))
            architectural_decisions.append(
                {
                    "title": title,
                    "rationale": body,
                    "tradeoff": "",
                }
            )
        if not architectural_decisions:
            for heading_match in re.finditer(
                r"###\s+(?P<title>.+?)\n(?P<body>.*?)(?=###\s+|\Z)",
                decisions_body,
                re.DOTALL,
            ):
                architectural_decisions.append(
                    {
                        "title": _compact(heading_match.group("title")),
                        "rationale": _excerpt(heading_match.group("body"), limit=260),
                        "tradeoff": "",
                    }
                )

    # Challenges
    challenges: list[dict[str, str]] = []
    challenges_body = _find_section_text(
        sections,
        "critical challenges",
        "key decisions & challenges",
        "challenges",
        "struggles",
        "pain points",
    )
    if challenges_body:
        for bullet in _bullet_lines(challenges_body)[:6]:
            parts = bullet.split("→") if "→" in bullet else bullet.split(" - ", 1)
            if len(parts) >= 2:
                challenges.append(
                    {
                        "title": _compact(parts[0]),
                        "problem": _compact(parts[0]),
                        "solution": _compact(parts[1]),
                    }
                )
            else:
                challenges.append(
                    {
                        "title": _compact(bullet[:80]),
                        "problem": _compact(bullet),
                        "solution": "",
                    }
                )
        if not challenges:
            for heading_match in re.finditer(
                r"###\s+(?P<title>.+?)\n(?P<body>.*?)(?=###\s+|\Z)",
                challenges_body,
                re.DOTALL,
            ):
                challenges.append(
                    {
                        "title": _compact(heading_match.group("title")),
                        "problem": _excerpt(heading_match.group("body"), limit=200),
                        "solution": "",
                    }
                )

    # Tech oscillations
    tech_oscillations: list[dict[str, str]] = []
    oscillation_body = ""
    for key in ("tech oscillations", "technology changes", "stack evolution"):
        if key in sections:
            oscillation_body = sections[key]
            break
    if oscillation_body:
        for line in _bullet_lines(oscillation_body)[:6]:
            arrow_match = re.match(r"(.+?)\s*→\s*(.+?)(?:\s*\((.+?)\))?$", line)
            if arrow_match:
                tech_oscillations.append(
                    {
                        "original": _compact(arrow_match.group(1)),
                        "replacement": _compact(arrow_match.group(2)),
                        "problem": "",
                        "context": _compact(arrow_match.group(3) or ""),
                    }
                )

    # Timeline ASCII
    timeline_ascii = ""
    for key in ("timeline", "commit timeline", "ascii timeline"):
        if key in sections:
            code_match = re.search(r"```[^\n]*\n(.*?)```", sections[key], re.DOTALL)
            if code_match:
                timeline_ascii = code_match.group(1).strip()
            break

    # Code metrics
    code_metrics: dict[str, str] = {}
    metrics_body = _find_section_text(sections, "code metrics", "statistics", "metrics")
    if metrics_body:
        for line in _bullet_lines(metrics_body):
            kv = re.match(r"(.+?):\s*(.+)", line)
            if kv:
                code_metrics[_compact(kv.group(1)).lower()] = _compact(kv.group(2))

    # Tech stack
    tech_stack: list[str] = []
    tech_sections = [
        _find_section_text(sections, "tech stack"),
        _find_section_text(sections, "frontend stack"),
        _find_section_text(sections, "backend stack"),
        _find_section_text(sections, "deployment stack"),
    ]
    for block in tech_sections:
        if not block:
            continue
        for bullet in _bullet_lines(block):
            parts = bullet.split(":", 1)
            items_text = parts[1] if len(parts) == 2 else parts[0]
            tech_stack.extend(
                item.strip()
                for item in re.split(r",\s*", _compact(items_text))
                if item.strip()
            )
    if not tech_stack:
        languages = meta.get("languages")
        if languages:
            tech_stack.extend(item.strip() for item in languages.split(",") if item.strip())

    learnings: list[str] = []
    learnings_body = _find_section_text(sections, "key learnings", "lessons")
    if learnings_body:
        learnings = _bullet_lines(learnings_body)[:6]

    # Only return if we have meaningful content
    if not exec_summary and not phases:
        return None

    return {
        "project_title": project_title or header_title,
        "project_meta": meta,
        "executive_summary": exec_summary,
        "phases": phases,
        "architecture_diagrams": architecture_diagrams,
        "architectural_decisions": architectural_decisions[:6],
        "challenges": challenges[:6],
        "tech_oscillations": tech_oscillations[:6],
        "timeline_ascii": timeline_ascii,
        "code_metrics": code_metrics,
        "tech_stack": _dedupe_strings(tech_stack),
        "learnings": learnings,
    }


def _thought_garden(job_hunt_text: str, personal_bible_text: str) -> list[dict[str, str]]:
    sections = _section_map(job_hunt_text)
    interests = _bullet_lines(sections.get("my interests (for company matching)"))
    bible_sections = _section_map(personal_bible_text)
    motivation = _bullet_lines(bible_sections.get("what motivates him"))
    topics = interests[:5] + motivation[:3]
    return [
        {
            "title": topic,
            "summary": _excerpt(
                f"This theme shows up repeatedly in Ahmad's current work, job search, and long-term product interests: {topic}.",
                limit=180,
            ),
        }
        for topic in topics[:6]
    ]


def _timeline_highlights(timeline: list[dict[str, str]]) -> list[str]:
    return [f"{item['year']}: {item['event']}" for item in timeline[:8]]


def _profile_daily_update_window(projects: list[ProjectCase]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for project in projects:
        window = dict(project.daily_update_window or {})
        for item in list(window.get("items") or [])[:1]:
            headline = _compact(item.get("headline"))
            summary = _compact(item.get("summary"))
            if not headline or not summary:
                continue
            items.append(
                {
                    "project_slug": project.slug,
                    "project_title": project.title,
                    "headline": headline,
                    "summary": summary,
                    "timestamp_label": _compact(item.get("timestamp_label")) or "Curated update",
                    "evidence_refs": list(item.get("evidence_refs") or []),
                }
            )
    return {
        "title": "Latest brain updates",
        "style": "slider",
        "items": items[:4],
    }


def _project_match_score(project: dict[str, Any], overlay: dict[str, Any]) -> int:
    project_slug = _slugify(project.get("slug") or project.get("title"))
    overlay_slug = _slugify(overlay.get("slug") or overlay.get("title"))
    project_title = _compact(project.get("title"))
    overlay_title = _compact(overlay.get("title"))
    if project_slug and overlay_slug and project_slug == overlay_slug:
        return 10
    if project_title and overlay_title and project_title.lower() == overlay_title.lower():
        return 10
    score = 0
    if (
        project_slug
        and overlay_slug
        and (project_slug in overlay_slug or overlay_slug in project_slug)
    ):
        score += 5
    project_tokens = {
        token for token in re.findall(r"[a-z0-9]{4,}", f"{project_title} {project_slug}".lower())
    }
    overlay_tokens = {
        token for token in re.findall(r"[a-z0-9]{4,}", f"{overlay_title} {overlay_slug}".lower())
    }
    score += len(project_tokens & overlay_tokens)
    return score


def _overlay_signal_text(overlay: dict[str, Any]) -> str:
    parts = [
        overlay.get("what_changed"),
        overlay.get("remaining"),
        overlay.get("latest_closeout"),
        *(overlay.get("recent_updates") or [])[:2],
    ]
    return _excerpt(" ".join(part for part in parts if part), limit=220)


async def _load_live_project_overlay(session: AsyncSession) -> list[dict[str, Any]]:
    project_notes = await store.list_project_notes(session, limit=12)
    overlay_items: list[dict[str, Any]] = []
    for note in project_notes:
        snapshot = await store.get_project_state_snapshot(session, note.id)
        recent_activity = await store.list_recent_activity(
            session, project_note_id=note.id, limit=8
        )
        if not snapshot and not recent_activity:
            continue
        latest_closeout = next(
            (
                item
                for item in recent_activity
                if getattr(item, "entry_type", "") == "session_closeout"
            ),
            None,
        )
        recent_updates: list[str] = []
        recent_titles: list[str] = []
        for entry in recent_activity:
            summary = _compact(
                getattr(entry, "summary", None)
                or getattr(entry, "title", None)
                or getattr(entry, "outcome", None)
                or getattr(entry, "open_question", None)
                or ""
            )
            if summary and summary not in recent_updates:
                recent_updates.append(summary)
            title = _compact(getattr(entry, "title", None) or "")
            if title and title not in recent_titles:
                recent_titles.append(title)
        overlay_items.append(
            {
                "project_note_id": str(note.id),
                "slug": _slugify(note.title),
                "title": note.title,
                "status": snapshot.status if snapshot else note.status,
                "active_score": round(float(snapshot.active_score), 3)
                if snapshot and snapshot.active_score is not None
                else None,
                "implemented": _excerpt(snapshot.implemented, limit=220) if snapshot else "",
                "remaining": _excerpt(snapshot.remaining, limit=220) if snapshot else "",
                "what_changed": _excerpt(snapshot.what_changed, limit=220) if snapshot else "",
                "holes": list((snapshot.holes or [])[:4]) if snapshot else [],
                "blockers": list((snapshot.blockers or [])[:4]) if snapshot else [],
                "last_signal_at": format_display_datetime(snapshot.last_signal_at)
                if snapshot
                else "",
                "latest_closeout": _excerpt(
                    getattr(latest_closeout, "summary", None)
                    or getattr(latest_closeout, "title", None)
                    or getattr(latest_closeout, "outcome", None)
                    or "",
                    limit=220,
                )
                if latest_closeout
                else "",
                "latest_closeout_at": format_display_datetime(
                    getattr(latest_closeout, "happened_at", None)
                )
                if latest_closeout
                else "",
                "recent_updates": recent_updates[:4],
                "recent_titles": recent_titles[:4],
            }
        )
    overlay_items.sort(
        key=lambda item: (
            float(item.get("active_score") or 0.0),
            str(item.get("last_signal_at") or ""),
            str(item.get("title") or ""),
        ),
        reverse=True,
    )
    return overlay_items


def _merge_live_project_overlay(
    read_models: dict[str, dict[str, Any]], live_overlay: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    if not live_overlay:
        return read_models

    overview = dict(read_models.get("profile:overview") or {})
    projects = dict(read_models.get("profile:projects") or {})
    coverage = dict(read_models.get("profile:coverage") or {})
    sources = dict(read_models.get("profile:sources") or {})
    library = dict(read_models.get("profile:library") or {})

    current_arc = dict(overview.get("current_arc") or {})
    project_items = [dict(item) for item in list(projects.get("items") or [])]
    matched_overlay_slugs: set[str] = set()
    live_focus: list[str] = []
    live_project_cards: list[dict[str, Any]] = []

    for project in project_items:
        best_overlay: dict[str, Any] | None = None
        best_score = 0
        for overlay in live_overlay:
            score = _project_match_score(project, overlay)
            if score > best_score:
                best_score = score
                best_overlay = overlay
        if not best_overlay or best_score < 4:
            continue

        matched_overlay_slugs.add(str(best_overlay.get("slug") or ""))
        signal_text = _overlay_signal_text(best_overlay)
        project.update(
            {
                "live_project_ref": best_overlay.get("project_note_id"),
                "live_status": best_overlay.get("status"),
                "live_active_score": best_overlay.get("active_score"),
                "live_implemented": best_overlay.get("implemented"),
                "live_remaining": best_overlay.get("remaining"),
                "live_what_changed": best_overlay.get("what_changed"),
                "live_holes": list(best_overlay.get("holes") or []),
                "live_blockers": list(best_overlay.get("blockers") or []),
                "latest_closeout": best_overlay.get("latest_closeout"),
                "latest_closeout_at": best_overlay.get("latest_closeout_at"),
                "recent_updates": list(best_overlay.get("recent_updates") or []),
                "recent_titles": list(best_overlay.get("recent_titles") or []),
            }
        )
        if signal_text:
            live_focus.append(f"{project.get('title')}: {signal_text}")
        live_project_cards.append(
            {
                "title": project.get("title"),
                "slug": project.get("slug"),
                "status": best_overlay.get("status"),
                "active_score": best_overlay.get("active_score"),
                "what_changed": best_overlay.get("what_changed"),
                "remaining": best_overlay.get("remaining"),
                "latest_closeout": best_overlay.get("latest_closeout"),
                "last_signal_at": best_overlay.get("last_signal_at"),
            }
        )

    unmatched_live_projects = [
        {
            "title": item.get("title"),
            "slug": item.get("slug"),
            "status": item.get("status"),
            "active_score": item.get("active_score"),
            "summary": _overlay_signal_text(item),
            "last_signal_at": item.get("last_signal_at"),
        }
        for item in live_overlay
        if str(item.get("slug") or "") not in matched_overlay_slugs
    ]

    if live_focus:
        current_arc["focus"] = _dedupe_strings(list(current_arc.get("focus") or []) + live_focus)[
            :6
        ]
    current_arc["live_projects"] = live_project_cards[:4]

    overview["current_arc"] = current_arc
    overview["flagship_projects"] = project_items[:4]
    projects["items"] = project_items
    sources["live_project_overlay"] = {
        "count": len(live_overlay),
        "matched_case_studies": len(matched_overlay_slugs),
        "unmapped_projects": unmatched_live_projects[:6],
    }
    coverage["live_projects_without_case_study"] = unmatched_live_projects[:6]
    library["live_overlay"] = {
        "summary": "Profile read models now blend long-span biography with live project-state evidence from the private brain.",
        "matched_projects": len(matched_overlay_slugs),
        "unmapped_projects": len(unmatched_live_projects),
    }

    read_models["profile:overview"] = overview
    read_models["profile:projects"] = projects
    read_models["profile:coverage"] = coverage
    read_models["profile:sources"] = sources
    read_models["profile:library"] = library
    return read_models


def build_profile_narrative() -> dict[str, Any]:
    seed_dir = resolve_public_seed_path()
    personal_bible_path = seed_dir / "Ahmad_Personal_Bible.md"
    job_hunt_path = seed_dir / "Job_Hunt_Summary_Mar2026.md"
    project_descriptions_path = seed_dir / "Project_Descriptions_Improved.md"
    photo_guide_path = seed_dir / "Website_Photo_Guide.md"
    brain_dump_path = seed_dir / "brain_data_dump_mar16.md"
    signal_path = seed_dir / "Ahmad_Profile_Signal.md"
    repo_history_paths = sorted(seed_dir.glob("repo_history_*.md"))

    personal_bible_text = _read_text(personal_bible_path)
    job_hunt_text = _read_text(job_hunt_path)
    project_descriptions_text = _read_text(project_descriptions_path)
    photo_guide_text = _read_text(photo_guide_path)
    brain_dump_text = _read_text(brain_dump_path)

    professional_summary, projects = _parse_project_descriptions(project_descriptions_text)
    repo_histories = _parse_repo_histories(seed_dir)
    projects = _curate_public_projects(
        projects,
        personal_bible_text=personal_bible_text,
        repo_histories=repo_histories,
    )
    roles = _parse_roles(job_hunt_text)
    education = _parse_education(job_hunt_text)
    skills = _parse_skill_sections(job_hunt_text)
    timeline = _timeline_from_personal_bible(personal_bible_text)
    eras = _eras_from_personal_bible(personal_bible_text)
    capabilities = _parse_capabilities(personal_bible_text)
    person = _kv_lines(_section_map(personal_bible_text).get("the person"))
    photo_assets = _photo_assets(seed_dir, photo_guide_text)
    photos = _photo_selection(photo_assets)
    identity_stack = _identity_stack(job_hunt_text, professional_summary)
    current_arc = _current_arc(personal_bible_text, brain_dump_text, projects)
    proof_points = _proof_points(personal_bible_text)
    contact_modes = _contact_modes(personal_bible_text)
    personal_texture = _personal_texture(personal_bible_text)
    thought_garden = _thought_garden(job_hunt_text, personal_bible_text)
    currently = _currently_feed(seed_dir)
    personal_signals = _personal_signals(person, personal_texture, currently)
    resume_sections = _resume_sections(professional_summary, roles, education, skills, projects)
    photo_slots = _photo_slots(photos)
    taste_modules = _taste_modules(seed_dir)
    daily_update_window = _profile_daily_update_window(projects)
    latest_work_summary = _excerpt(
        " ".join(
            item.get("summary") or ""
            for item in list(daily_update_window.get("items") or [])[:2]
        )
        or " ".join(project.latest_work_summary for project in projects[:2] if project.latest_work_summary),
        limit=260,
    )

    faq = [
        {
            "question": "What kind of work fits Ahmad best right now?",
            "answer": "High-ownership engineering roles where distributed systems, AI-native product building, and real product mission all matter at once.",
        },
        {
            "question": "What is duSraBheja?",
            "answer": next(
                (project.summary for project in projects if project.slug == "dusrabheja"), ""
            ),
        },
        {
            "question": "Why is the site structured like a life story instead of a normal portfolio?",
            "answer": "Because the source material is not just a resume. It spans undergrad, immigration, Amazon-scale systems work, freelance delivery, and current AI product building.",
        },
    ]

    return {
        "name": person.get("full name") or settings.public_profile_name,
        "preferred_name": person.get("goes by") or settings.public_profile_short_name,
        "location": person.get("current base") or settings.public_profile_location,
        "professional_summary": professional_summary,
        "hero_summary": identity_stack[0] if identity_stack else professional_summary,
        "identity_stack": identity_stack,
        "current_arc": current_arc,
        "resume_sections": resume_sections,
        "eras": [asdict(era) for era in eras],
        "timeline": timeline,
        "timeline_highlights": _timeline_highlights(timeline),
        "roles": [asdict(role) for role in roles],
        "education": education,
        "skills": skills,
        "projects": [asdict(project) for project in projects],
        "capabilities": [asdict(book) for book in capabilities],
        "contact_modes": [asdict(item) for item in contact_modes],
        "proof_points": proof_points,
        "personal_texture": personal_texture,
        "personal_signals": personal_signals,
        "thought_garden": thought_garden,
        "taste_modules": taste_modules,
        "photos": photos,
        "photo_slots": photo_slots,
        "faq": faq,
        "currently": currently,
        "daily_update_window": daily_update_window,
        "latest_work_summary": latest_work_summary,
        "open_brain_topics": list(_OPEN_BRAIN_TOPICS),
        "repo_histories": repo_histories,
        "source_pack": {
            "seed_dir": str(seed_dir),
            "files": [
                str(personal_bible_path),
                str(job_hunt_path),
                str(project_descriptions_path),
                str(photo_guide_path),
                str(brain_dump_path),
                str(signal_path),
                *[str(path) for path in repo_history_paths],
            ],
        },
    }


async def _source_counts(session: AsyncSession) -> list[dict[str, Any]]:
    result = await session.execute(
        select(SyncSource.source_type, func.count(SourceItem.id))
        .select_from(SyncSource)
        .join(SourceItem, SourceItem.sync_source_id == SyncSource.id, isouter=True)
        .group_by(SyncSource.source_type)
        .order_by(func.count(SourceItem.id).desc(), SyncSource.source_type.asc())
    )
    return [
        {"source_type": source_type, "items": int(count or 0)}
        for source_type, count in result.all()
    ]


async def _keyword_count(session: AsyncSession, term: str) -> dict[str, int]:
    lowered = f"%{term.lower()}%"
    note_result = await session.execute(
        select(func.count(Note.id)).where(
            or_(
                func.lower(func.coalesce(Note.title, "")).like(lowered),
                func.lower(func.coalesce(Note.content, "")).like(lowered),
            )
        )
    )
    artifact_result = await session.execute(
        select(func.count(Artifact.id)).where(
            or_(
                func.lower(func.coalesce(Artifact.summary, "")).like(lowered),
                func.lower(func.coalesce(Artifact.raw_text, "")).like(lowered),
            )
        )
    )
    source_result = await session.execute(
        select(func.count(SourceItem.id)).where(
            or_(
                func.lower(func.coalesce(SourceItem.title, "")).like(lowered),
                func.lower(func.coalesce(SourceItem.summary, "")).like(lowered),
            )
        )
    )
    return {
        "notes": int(note_result.scalar() or 0),
        "artifacts": int(artifact_result.scalar() or 0),
        "source_items": int(source_result.scalar() or 0),
    }


async def _coverage_gaps(session: AsyncSession, narrative: dict[str, Any]) -> list[CoverageGap]:
    counts = await _source_counts(session)
    by_source = {item["source_type"]: item["items"] for item in counts}
    iitkgp = await _keyword_count(session, "iitkgp")
    if not any(iitkgp.values()):
        iitkgp = await _keyword_count(session, "iit kharagpur")
    nyu = await _keyword_count(session, "nyu")
    amazon = await _keyword_count(session, "amazon")

    gaps: list[CoverageGap] = []
    if not any(iitkgp.values()):
        gaps.append(
            CoverageGap(
                slug="iitkgp",
                title="IIT Kharagpur coverage is still missing in the live brain",
                severity="high",
                summary="The public source pack has the IIT KGP chapter, but the private brain has little to no searchable IIT-specific evidence.",
                recommendation="Ingest undergrad folders, thesis/project archives, notes, and any old documents from local storage or Google exports.",
            )
        )
    if sum(by_source.values()) < 150:
        gaps.append(
            CoverageGap(
                slug="thin-long-span-history",
                title="Long-span life history is still thin",
                severity="high",
                summary="The live brain has relatively few imported source items compared with the breadth of Ahmad's career and academic history.",
                recommendation="Run the missing long-span imports: Google Takeout, Drive, Keep, Gmail, YouTube/Search history, and academic project folders.",
            )
        )
    if not any(nyu.values()):
        gaps.append(
            CoverageGap(
                slug="nyu",
                title="NYU coverage needs strengthening",
                severity="medium",
                summary="The narrative clearly includes NYU, but the private knowledge base lacks enough course/project evidence to make that chapter rich.",
                recommendation="Import class notes, projects, and NYU coursework exports from the MacBook and Drive.",
            )
        )
    if not any(amazon.values()):
        gaps.append(
            CoverageGap(
                slug="amazon",
                title="Amazon evidence is lighter than the story requires",
                severity="medium",
                summary="There are Amazon references in the source pack, but the live brain needs more durable artifacts and reflections to support a deep work-history view.",
                recommendation="Import sanitized notes, docs, and personal reflections that summarize systems work without exposing confidential material.",
            )
        )
    if not gaps:
        gaps.append(
            CoverageGap(
                slug="curation",
                title="Coverage is present but still needs curation",
                severity="medium",
                summary="Signals exist, but they are not yet organized into expertise, institutions, eras, and proof-rich chapters.",
                recommendation="Continue materializing curated read models from existing evidence before expanding ingestion again.",
            )
        )
    return gaps


async def build_profile_read_models(session: AsyncSession) -> dict[str, dict[str, Any]]:
    narrative = build_profile_narrative()
    inventory = build_profile_inventory_payload()
    source_counts = await _source_counts(session)
    coverage_gaps = await _coverage_gaps(session, narrative)
    notes_count = len(await store.list_notes(session, limit=500))
    projects = narrative.get("projects") or []
    live_project_overlay = await _load_live_project_overlay(session)

    overview = {
        "headline": narrative.get("hero_summary") or settings.public_site_title,
        "summary": narrative.get("professional_summary") or narrative.get("hero_summary"),
        "current_arc": narrative.get("current_arc"),
        "identity_stack": narrative.get("identity_stack"),
        "key_metrics": [
            {"label": "Years of experience", "value": "6+"},
            {"label": "Live flagship projects", "value": str(len(projects[:4]))},
            {"label": "Tracked notes", "value": str(notes_count)},
            {"label": "Source systems", "value": str(len(source_counts))},
        ],
        "flagship_projects": projects[:4],
        "photos": narrative.get("photos"),
    }
    timeline = {
        "eras": narrative.get("eras"),
        "events": narrative.get("timeline"),
    }
    identity = {
        "headline": narrative.get("hero_summary") or settings.public_site_title,
        "identity_stack": narrative.get("identity_stack"),
        "current_arc": narrative.get("current_arc"),
        "personal_texture": narrative.get("personal_texture"),
        "contact_modes": narrative.get("contact_modes"),
    }
    institutions = {
        "items": [
            {
                "title": item.get("title"),
                "years": item.get("years"),
                "summary": item.get("summary"),
                "institutions": list(item.get("institutions") or []),
                "roles": list(item.get("roles") or []),
            }
            for item in list(narrative.get("eras") or [])
        ]
    }
    expertise = {
        "books": narrative.get("capabilities"),
        "library_mapping": {
            "line": "evidence or observation",
            "chapter": "era, project, or major arc",
            "book": "domain, capability, or expertise area",
            "library": "Ahmad's full private knowledge system",
        },
    }
    project_cases = {
        "items": projects,
    }
    sources = {
        "seed_pack": narrative.get("source_pack"),
        "live_source_counts": source_counts,
        "inventory": inventory,
        "advice": [
            "Keep CompanyInterviewPrep as the public-safe narrative source pack.",
            "Use local and Google exports to deepen historical coverage.",
            "Attach imported evidence to institutions, eras, and projects during ingest.",
        ],
    }
    coverage = {
        "gaps": [asdict(item) for item in coverage_gaps],
        "expected_chapters": [era["title"] for era in narrative.get("eras", [])],
        "institution_hits": {
            "iitkgp": await _keyword_count(session, "iitkgp"),
            "nyu": await _keyword_count(session, "nyu"),
            "amazon": await _keyword_count(session, "amazon"),
        },
        "inventory": inventory,
    }
    library = {
        "principle": "The library should explain the person, not just list machine-derived objects.",
        "read_surfaces": [
            "Overview",
            "Timeline",
            "Expertise",
            "Projects",
            "Sources",
            "Coverage",
            "Library",
        ],
        "summary": "The current system has strong raw storage primitives. The missing layer is curated meaning: eras, expertise books, institutions, and proof-backed chapters.",
    }
    read_models = {
        "profile:overview": overview,
        "profile:identity": identity,
        "profile:timeline": timeline,
        "profile:institutions": institutions,
        "profile:expertise": expertise,
        "profile:projects": project_cases,
        "profile:sources": sources,
        "profile:coverage": coverage,
        "profile:library": library,
    }
    return _merge_live_project_overlay(read_models, live_project_overlay)


async def materialize_profile_read_models(
    session: AsyncSession, *, force: bool = False
) -> dict[str, dict[str, Any]]:
    existing = {
        record.capability_key: record
        for record in await store.list_capability_records(session, limit=100)
        if record.capability_key.startswith("profile:")
    }
    stale = force or not existing
    if not stale:
        newest = max(
            (record.updated_at for record in existing.values() if record.updated_at), default=None
        )
        stale = newest is None or newest < (_utcnow() - PUBLIC_MODEL_TTL)
    if stale:
        payloads = await build_profile_read_models(session)
        titles = {
            "profile:overview": "Profile Overview",
            "profile:identity": "Identity Stack",
            "profile:timeline": "Life Timeline",
            "profile:institutions": "Institution Chapters",
            "profile:expertise": "Expertise Books",
            "profile:projects": "Project Cases",
            "profile:sources": "Source Inventory",
            "profile:coverage": "Coverage Report",
            "profile:library": "Library Meaning",
        }
        summaries = {
            "profile:overview": "High-level narrative and current arc.",
            "profile:identity": "Identity, contact, and public-facing self description.",
            "profile:timeline": "Life-story timeline across eras and milestones.",
            "profile:institutions": "Institution and work-history chapters mapped from the timeline.",
            "profile:expertise": "Capability books derived from narrative source material.",
            "profile:projects": "Curated proof-rich project case studies.",
            "profile:sources": "Narrative source pack and live source counts.",
            "profile:coverage": "Coverage gaps and ingestion priorities.",
            "profile:library": "Library metaphor and read-surface framing.",
        }
        for key, payload in payloads.items():
            await store.upsert_capability_record(
                session,
                capability_key=key,
                title=titles[key],
                summary=summaries[key],
                protocol="profile",
                visibility="private",
                payload=payload,
                metadata_={"materialized_from": "company_interview_prep"},
            )
    records = {
        record.capability_key: record
        for record in await store.list_capability_records(session, limit=100)
        if record.capability_key.startswith("profile:")
    }
    return {key: dict((record.payload or {})) for key, record in records.items()}


async def get_profile_read_model(session: AsyncSession, capability_key: str) -> dict[str, Any]:
    payloads = await materialize_profile_read_models(session)
    return dict(payloads.get(capability_key) or {})
