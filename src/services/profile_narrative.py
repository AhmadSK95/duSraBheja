"""Structured public/profile narrative built from the CompanyInterviewPrep source pack."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.lib import store
from src.models import Artifact, Note, SourceItem, SyncSource
from src.services.profile_inventory import build_profile_inventory_payload

PUBLIC_MODEL_TTL = timedelta(minutes=20)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def resolve_public_seed_path() -> Path:
    configured = Path(settings.public_profile_seed_path).expanduser()
    if configured.exists():
        return configured
    mounted = Path("/public-seed")
    if mounted.exists():
        return mounted
    return configured


def _slugify(value: str | None) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower())
    return cleaned.strip("-")


def public_asset_path(filename: str) -> Path | None:
    safe_name = Path(filename).name
    if not safe_name or safe_name != filename:
        return None
    candidate = resolve_public_seed_path() / "website_photos" / safe_name
    return candidate if candidate.exists() else None


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


def _section_map(text: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for _level, title, body in _extract_markdown_sections(text):
        mapping[title.strip().lower()] = body.strip()
    return mapping


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
        match = re.match(r"^\*\*(.+?):\*\*\s*(.+)$", stripped) or re.match(r"^\*\*(.+?)\*\*:\s*(.+)$", stripped)
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
    stack: list[str] = field(default_factory=list)
    resume_bullets: list[str] = field(default_factory=list)
    body: str = ""
    demonstrates: list[str] = field(default_factory=list)
    links: list[dict[str, str]] = field(default_factory=list)
    proof: list[str] = field(default_factory=list)


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
        best_for = [part.strip() for part in re.split(r",\s*", fields.get("best for", "")) if part.strip()]
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
        "hero": pick("05_nov2025_waterfront_fullbody_portrait.jpg"),
        "personality": pick("09_aug2025_holding_oscar_colorful_art_wall.jpg"),
        "work": pick("02_feb2026_nyc_street_portrait_with_badge.jpg"),
        "contact": pick("03_jan2026_oscar_on_shoulder_white_wall.jpg"),
        "home": pick("01_feb2026_home_selfie_with_oscar_and_iris.jpg"),
        "gallery": [asset.as_dict() for asset in sorted(assets.values(), key=lambda item: item.filename)][:8],
    }


def _parse_project_descriptions(text: str) -> tuple[str, list[ProjectCase]]:
    sections = list(re.finditer(r"^###\s+(?P<title>.+?)\n(?P<body>.*?)(?=^###\s+|\Z)", text, re.MULTILINE | re.DOTALL))
    summary_match = re.search(
        r"##\s+PROFESSIONAL SUMMARY.*?\n\n(?P<body>.*?)(?=\n##\s+KEY PROJECTS|\Z)",
        text,
        re.DOTALL,
    )
    professional_summary = _excerpt(summary_match.group("body") if summary_match else "", limit=1000)
    projects: list[ProjectCase] = []
    for match in sections:
        title = _compact(match.group("title"))
        body = match.group("body")
        slug = _slugify(title.split(" - ", 1)[0])
        resume_match = re.search(r"\*\*Resume.*?\*\*:\s*(?P<body>.*?)(?=\n\*\*LinkedIn|\Z)", body, re.DOTALL)
        linkedin_match = re.search(r"\*\*LinkedIn.*?\*\*:\s*(?P<body>.*?)(?=\n\*\*What this project demonstrates|\Z)", body, re.DOTALL)
        demonstrates_match = re.search(r"\*\*What this project demonstrates:\*\*\s*(?P<body>.*)$", body, re.DOTALL)
        resume_bullets = _bullet_lines(resume_match.group("body") if resume_match else "")
        demonstrates = _bullet_lines(demonstrates_match.group("body") if demonstrates_match else "")
        full_body = _compact(linkedin_match.group("body") if linkedin_match else body)
        stack = []
        stack_match = re.search(r"Stack:\s*(?P<value>.+?)(?:\.|$)", body)
        if stack_match:
            stack = [item.strip() for item in stack_match.group("value").split(",") if item.strip()]
        status = "Live client project" if "live client project" in title.lower() else "Active build"
        links: list[dict[str, str]] = []
        live_url = _find_url(body)
        if live_url:
            links.append({"label": "Live site", "href": live_url})
        projects.append(
            ProjectCase(
                slug=slug,
                title=title,
                tagline=resume_bullets[0] if resume_bullets else _excerpt(full_body, limit=180),
                summary=_excerpt(full_body, limit=340),
                status=status,
                stack=stack,
                resume_bullets=resume_bullets,
                body=full_body,
                demonstrates=demonstrates,
                links=links,
                proof=resume_bullets[:2] + demonstrates[:2],
            )
        )
    return professional_summary, projects


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
            summary=_excerpt(sections.get("iit kharagpur — b.tech, electrical engineering (2013–2017)"), limit=340),
            highlights=_bullet_lines(sections.get("iit kharagpur — b.tech, electrical engineering (2013–2017)"))[:5],
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
                        sections.get("loylty rewardz — management trainee → software engineer (july 2017 – april 2020, mumbai)", ""),
                    ]
                ),
                limit=340,
            ),
            highlights=_bullet_lines(sections.get("loylty rewardz — management trainee → software engineer (july 2017 – april 2020, mumbai)"))[:5],
            institutions=["Citicorp Services", "Loylty Rewardz"],
            roles=["Intern", "Management Trainee", "Software Engineer"],
        ),
        LifeEra(
            slug="nyu",
            title="NYU Tandon",
            years="2021-2022",
            summary=_excerpt(sections.get("nyu tandon school of engineering — m.s., electrical engineering (2021–2022)"), limit=340),
            highlights=_bullet_lines(sections.get("nyu tandon school of engineering — m.s., electrical engineering (2021–2022)"))[:5],
            institutions=["NYU Tandon"],
            roles=["Graduate Student"],
        ),
        LifeEra(
            slug="amazon",
            title="Amazon",
            years="2022-2025",
            summary=_excerpt(sections.get("amazon — software development engineer (june 2022 – september 2025, nyc)"), limit=340),
            highlights=_bullet_lines(sections.get("amazon — software development engineer (june 2022 – september 2025, nyc)"))[:5],
            institutions=["Amazon"],
            roles=["Software Development Engineer"],
        ),
        LifeEra(
            slug="builder-phase",
            title="Independent Builder Phase",
            years="2025-Present",
            summary=_excerpt(sections.get("part 3: the builder phase (sep 2025 – present)"), limit=340),
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


def _current_arc(personal_bible_text: str, brain_dump_text: str, projects: list[ProjectCase]) -> dict[str, Any]:
    bible_sections = _section_map(personal_bible_text)
    dump_sections = _section_map(brain_dump_text)
    return {
        "title": "Current Arc",
        "summary": _excerpt(
            dump_sections.get("why i want to join narrative")
            or bible_sections.get("act 3 — the builder (2025-present):")
            or bible_sections.get("part 8: the narrative arc (for the website)"),
            limit=420,
        ),
        "focus": [
            "Building duSraBheja into a trustworthy memory and project-state system.",
            "Turning dataGenie into a serious analytics product for non-technical users.",
            "Looking for work where technical depth and product conviction actually align.",
        ],
        "flagship_projects": [project.slug for project in projects[:3]],
    }


def _proof_points(personal_bible_text: str) -> list[dict[str, str]]:
    website_signals = _extract_markdown_sections(_section_body(personal_bible_text, "Part 9: Website Content Signals"))
    proofs: list[dict[str, str]] = []
    for _level, title, body in website_signals:
        bullets = _bullet_lines(body)
        if not bullets:
            continue
        proofs.append(
            {
                "title": title,
                "summary": _excerpt(body, limit=220),
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
            value=(settings.public_contact_linkedin_url or person.get("linkedin", "")).replace("https://", ""),
            href=_normalize_public_href(settings.public_contact_linkedin_url or person.get("linkedin", "")),
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
            value=(settings.public_contact_instagram_url or person.get("instagram", "")).replace("https://", ""),
            href=_normalize_public_href(settings.public_contact_instagram_url or person.get("instagram", "")),
            note="Human context, life moments, and Oscar content.",
        ),
    ]
    return [entry for entry in entries if entry.value and entry.href]


def _personal_texture(personal_bible_text: str) -> list[str]:
    sections = _section_map(personal_bible_text)
    interests = _bullet_lines(sections.get("interests & passions"))
    personal = _split_paragraphs(sections.get("part 5.5: the personal life"))
    texture = [
        "Five languages across South Indian roots and a New York-based engineering career.",
        "Cat dad to Oscar and Iris; Oscar is basically the public mascot.",
        "Cycling, fitness, hip hop, Indian film music, and Japanese markets show up repeatedly in the source material.",
    ]
    texture.extend(interests[:4])
    texture.extend(personal[:2])
    deduped: list[str] = []
    seen: set[str] = set()
    for item in texture:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped[:8]


def _thought_garden(job_hunt_text: str, personal_bible_text: str) -> list[dict[str, str]]:
    sections = _section_map(job_hunt_text)
    interests = _bullet_lines(sections.get("my interests (for company matching)"))
    bible_sections = _section_map(personal_bible_text)
    motivation = _bullet_lines(bible_sections.get("what motivates him"))
    topics = interests[:5] + motivation[:3]
    return [
        {
            "title": topic,
            "summary": _excerpt(f"This theme shows up repeatedly in Ahmad's current work, job search, and long-term product interests: {topic}.", limit=180),
        }
        for topic in topics[:6]
    ]


def _timeline_highlights(timeline: list[dict[str, str]]) -> list[str]:
    return [f"{item['year']}: {item['event']}" for item in timeline[:8]]


def build_profile_narrative() -> dict[str, Any]:
    seed_dir = resolve_public_seed_path()
    personal_bible_path = seed_dir / "Ahmad_Personal_Bible.md"
    job_hunt_path = seed_dir / "Job_Hunt_Summary_Mar2026.md"
    project_descriptions_path = seed_dir / "Project_Descriptions_Improved.md"
    photo_guide_path = seed_dir / "Website_Photo_Guide.md"
    brain_dump_path = seed_dir / "brain_data_dump_mar16.md"

    personal_bible_text = _read_text(personal_bible_path)
    job_hunt_text = _read_text(job_hunt_path)
    project_descriptions_text = _read_text(project_descriptions_path)
    photo_guide_text = _read_text(photo_guide_path)
    brain_dump_text = _read_text(brain_dump_path)

    professional_summary, projects = _parse_project_descriptions(project_descriptions_text)
    roles = _parse_roles(job_hunt_text)
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

    faq = [
        {
            "question": "What kind of work fits Ahmad best right now?",
            "answer": "High-ownership engineering roles where distributed systems, AI-native product building, and real product mission all matter at once.",
        },
        {
            "question": "What is duSraBheja?",
            "answer": next((project.summary for project in projects if project.slug == "dusrabheja"), ""),
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
        "eras": [asdict(era) for era in eras],
        "timeline": timeline,
        "timeline_highlights": _timeline_highlights(timeline),
        "roles": [asdict(role) for role in roles],
        "projects": [asdict(project) for project in projects],
        "capabilities": [asdict(book) for book in capabilities],
        "contact_modes": [asdict(item) for item in contact_modes],
        "proof_points": proof_points,
        "personal_texture": personal_texture,
        "thought_garden": thought_garden,
        "photos": photos,
        "faq": faq,
        "source_pack": {
            "seed_dir": str(seed_dir),
            "files": [
                str(personal_bible_path),
                str(job_hunt_path),
                str(project_descriptions_path),
                str(photo_guide_path),
                str(brain_dump_path),
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
        "read_surfaces": ["Overview", "Timeline", "Expertise", "Projects", "Sources", "Coverage", "Library"],
        "summary": "The current system has strong raw storage primitives. The missing layer is curated meaning: eras, expertise books, institutions, and proof-backed chapters.",
    }
    return {
        "profile:overview": overview,
        "profile:timeline": timeline,
        "profile:expertise": expertise,
        "profile:projects": project_cases,
        "profile:sources": sources,
        "profile:coverage": coverage,
        "profile:library": library,
    }


async def materialize_profile_read_models(session: AsyncSession, *, force: bool = False) -> dict[str, dict[str, Any]]:
    existing = {
        record.capability_key: record
        for record in await store.list_capability_records(session, limit=100)
        if record.capability_key.startswith("profile:")
    }
    stale = force or not existing
    if not stale:
        newest = max((record.updated_at for record in existing.values() if record.updated_at), default=None)
        stale = newest is None or newest < (_utcnow() - PUBLIC_MODEL_TTL)
    if stale:
        payloads = await build_profile_read_models(session)
        titles = {
            "profile:overview": "Profile Overview",
            "profile:timeline": "Life Timeline",
            "profile:expertise": "Expertise Books",
            "profile:projects": "Project Cases",
            "profile:sources": "Source Inventory",
            "profile:coverage": "Coverage Report",
            "profile:library": "Library Meaning",
        }
        summaries = {
            "profile:overview": "High-level narrative and current arc.",
            "profile:timeline": "Life-story timeline across eras and milestones.",
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
