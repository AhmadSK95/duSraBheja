"""Public-facing site and chatbot routes."""

from __future__ import annotations

import html

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from src.api.public_ui import render_public_shell
from src.api.schemas import PublicChatRequest
from src.config import settings
from src.database import async_session
from src.services.profile_narrative import public_asset_path
from src.services.public_surface import (
    answer_public_question,
    get_public_answer_policy,
    get_public_profile,
    get_public_project,
    list_public_faq,
    list_public_projects,
)

router = APIRouter(tags=["public"])


def _profile_payload(profile: dict) -> dict:
    return dict(profile.get("payload") or {})


def _safe(value: object | None) -> str:
    return html.escape(str(value or ""))


def _photo_block(photo: dict | None, *, class_name: str = "public-photo-card", label: str | None = None) -> str:
    if not photo or not photo.get("url"):
        return ""
    caption = _safe(photo.get("title") or "")
    eyebrow = f'<div class="public-kicker">{_safe(label)}</div>' if label else ""
    description = _safe(photo.get("description") or photo.get("vibe") or "")
    return f"""
    <figure class="{class_name}">
      <img src="{_safe(photo.get('url'))}" alt="{caption}" loading="eager" />
      <figcaption>
        {eyebrow}
        <strong>{caption}</strong>
        <span>{description}</span>
      </figcaption>
    </figure>
    """


def _string_list(items: list[str], *, class_name: str = "public-bullet-list") -> str:
    rows = "".join(f"<li>{_safe(item)}</li>" for item in items if item)
    if not rows:
        return ""
    return f'<ul class="{class_name}">{rows}</ul>'


def _pill_list(items: list[str], *, class_name: str = "public-pill-list") -> str:
    rows = "".join(f'<span class="public-pill">{_safe(item)}</span>' for item in items if item)
    return f'<div class="{class_name}">{rows}</div>' if rows else ""


def _contact_rows(contacts: list[dict]) -> str:
    rows = []
    for item in contacts:
        href = item.get("href")
        if not href:
            continue
        rows.append(
            f"""
            <article class="public-contact-card">
              <div class="public-kicker">{_safe(item.get("label") or "Contact")}</div>
              <h3>{_safe(item.get("value") or item.get("label"))}</h3>
              <p>{_safe(item.get("note") or "")}</p>
              <a class="public-inline-link" href="{_safe(href)}" target="_blank" rel="noreferrer">Open channel</a>
            </article>
            """
        )
    return "".join(rows)


def _project_card(project: dict, *, detail: bool = False) -> str:
    payload = dict(project.get("payload") or {})
    stack = payload.get("stack") or project.get("stack") or []
    bullets = payload.get("resume_bullets") or project.get("resume_bullets") or []
    demonstrates = payload.get("demonstrates") or project.get("demonstrates") or []
    links = payload.get("links") or project.get("links") or []
    links_html = "".join(
        f'<a class="public-inline-link" href="{_safe(item.get("href"))}" target="_blank" rel="noreferrer">{_safe(item.get("label") or "Open")}</a>'
        for item in links
        if item.get("href")
    )
    detail_html = ""
    if detail:
        detail_html = (
            f'<div class="public-detail-block"><h3>How it is framed</h3>{_string_list(bullets[:4])}</div>'
            f'<div class="public-detail-block"><h3>What it demonstrates</h3>{_string_list(demonstrates[:4])}</div>'
        )
    return f"""
    <article class="public-case-card">
      <div class="public-kicker">{_safe(payload.get("status") or "Case Study")}</div>
      <h3>{_safe(project.get("title"))}</h3>
      <p>{_safe(payload.get("tagline") or project.get("summary") or "")}</p>
      {_pill_list(stack[:6], class_name="public-pill-list public-pill-list--tight")}
      {detail_html}
      <div class="public-link-row">
        <a class="public-inline-link" href="/projects/{_safe(project.get('slug'))}">Read case study</a>
        {links_html}
      </div>
    </article>
    """


def _timeline_cards(eras: list[dict]) -> str:
    return "".join(
        f"""
        <article class="public-timeline-card">
          <div class="public-timeline-rail"></div>
          <div class="public-timeline-content">
            <div class="public-kicker">{_safe(item.get("years"))}</div>
            <h3>{_safe(item.get("title"))}</h3>
            <p>{_safe(item.get("summary"))}</p>
            {_string_list(list(item.get("highlights") or [])[:4])}
          </div>
        </article>
        """
        for item in eras
    )


def _capability_cards(items: list[dict]) -> str:
    return "".join(
        f"""
        <article class="public-capability-card">
          <div class="public-kicker">Expertise Book</div>
          <h3>{_safe(item.get("title"))}</h3>
          <p>{_safe(item.get("summary"))}</p>
          {_string_list(list(item.get("chapters") or [])[:4])}
        </article>
        """
        for item in items[:4]
    )


def _proof_cards(items: list[dict]) -> str:
    return "".join(
        f"""
        <article class="public-proof-card">
          <div class="public-kicker">{_safe(item.get("title"))}</div>
          <p>{_safe(item.get("summary"))}</p>
          {_string_list(list(item.get("points") or [])[:4])}
        </article>
        """
        for item in items[:3]
    )


def _render_public_recent_signals(projects: list[dict]) -> str:
    rows = []
    for project in projects[:3]:
        payload = dict(project.get("payload") or {})
        summary = payload.get("tagline") or project.get("summary") or ""
        rows.append(
            f"""
            <article class="public-signal-card">
              <div class="public-kicker">Approved Signal</div>
              <strong>{_safe(project.get("title"))}</strong>
              <p>{_safe(summary)}</p>
            </article>
            """
        )
    return "".join(rows)


@router.get("/public-assets/profile/{filename}")
async def public_profile_asset(filename: str) -> FileResponse:
    path = public_asset_path(filename)
    if not path:
        raise HTTPException(status_code=404, detail="Public asset not found")
    return FileResponse(path)


@router.get("/", response_class=HTMLResponse)
async def public_home() -> HTMLResponse:
    async with async_session() as session:
        profile = await get_public_profile(session)
        projects = await list_public_projects(session)
    payload = _profile_payload(profile)
    hero_media = _photo_block((payload.get("photos") or {}).get("hero"), class_name="public-hero-visual", label="Hero Portrait")
    content = f"""
    <section class="public-editorial-band">
      <article class="public-story-card public-story-card--wide">
        <div class="public-kicker">Identity Stack</div>
        <h2>A living professional biography, not a template portfolio.</h2>
        <p>{_safe(payload.get("professional_summary") or payload.get("hero_summary") or profile.get("summary"))}</p>
        {_string_list(list(payload.get("identity_stack") or [])[:4])}
      </article>
      <article class="public-story-card">
        <div class="public-kicker">Current Arc</div>
        <h2>{_safe(((payload.get("current_arc") or {}).get("title")) or "What is happening now")}</h2>
        <p>{_safe(((payload.get("current_arc") or {}).get("summary")) or "")}</p>
        {_string_list(list((payload.get("current_arc") or {}).get("focus") or [])[:3])}
      </article>
    </section>
    <section class="public-section">
      <div class="public-section-heading">
        <div class="public-kicker">Selected Work</div>
        <h2>Proof of scope across AI systems, product thinking, and real delivery.</h2>
      </div>
      <div class="public-case-grid">
        {''.join(_project_card(project) for project in projects[:4])}
      </div>
    </section>
    <section class="public-editorial-band">
      <article class="public-story-card">
        <div class="public-kicker">Professional Read</div>
        <h2>What this body of work signals.</h2>
        <div class="public-proof-grid">{_proof_cards(list(payload.get("proof_points") or []))}</div>
      </article>
      <article class="public-story-card">
        <div class="public-kicker">Human Texture</div>
        <h2>The person behind the engineering.</h2>
        {_pill_list(list(payload.get("personal_texture") or [])[:6])}
        {_photo_block((payload.get("photos") or {}).get("personality"), label="Personality Anchor")}
      </article>
    </section>
    """
    return HTMLResponse(
        render_public_shell(
            page_title=f"{settings.public_profile_short_name} — Living Profile",
            hero_kicker="Living Profile",
            hero_title=f"{settings.public_profile_short_name} builds systems with memory, taste, and proof.",
            hero_subtitle=(payload.get("hero_summary") or profile.get("summary") or "").strip(),
            hero_media_html=hero_media,
            content_html=content,
            active_nav="home",
            page_data={"page": "home"},
            body_class="public-page-home",
        )
    )


@router.get("/about", response_class=HTMLResponse)
async def public_about() -> HTMLResponse:
    async with async_session() as session:
        profile = await get_public_profile(session)
    payload = _profile_payload(profile)
    hero_media = _photo_block((payload.get("photos") or {}).get("personality"), class_name="public-hero-visual", label="About Portrait")
    content = f"""
    <section class="public-editorial-band">
      <article class="public-story-card public-story-card--wide">
        <div class="public-kicker">Narrative Arc</div>
        <h2>A career built by moving into harder rooms and shipping real things.</h2>
        <p>{_safe(((payload.get("current_arc") or {}).get("summary")) or profile.get("summary") or "")}</p>
        {_string_list(list(payload.get("timeline_highlights") or [])[:6])}
      </article>
      <article class="public-story-card">
        <div class="public-kicker">Personal Notes</div>
        <h2>Selective public details.</h2>
        {_string_list(list(payload.get("personal_texture") or [])[:5])}
      </article>
    </section>
    <section class="public-section">
      <div class="public-section-heading">
        <div class="public-kicker">Chapters</div>
        <h2>The chapters that shape the whole picture.</h2>
      </div>
      <div class="public-timeline-grid">{_timeline_cards(list(payload.get("eras") or []))}</div>
    </section>
    <section class="public-section">
      <div class="public-section-heading">
        <div class="public-kicker">Roles</div>
        <h2>Career proof, condensed.</h2>
      </div>
      <div class="public-roles-grid">
        {''.join(
            f'''
            <article class="public-role-card">
              <div class="public-kicker">{_safe(item.get("period"))}</div>
              <h3>{_safe(item.get("organization"))}</h3>
              <strong>{_safe(item.get("title"))}</strong>
              <p>{_safe(item.get("summary"))}</p>
            </article>
            '''
            for item in list(payload.get("roles") or [])[:5]
        )}
      </div>
    </section>
    """
    return HTMLResponse(
        render_public_shell(
            page_title=f"About {settings.public_profile_short_name}",
            hero_kicker="About",
            hero_title="From IIT Kharagpur to New York to independent builder.",
            hero_subtitle="The site is arranged like a biography because the source material spans institutions, migration, work, and personal life rather than isolated portfolio bullets.",
            hero_media_html=hero_media,
            content_html=content,
            active_nav="about",
            page_data={"page": "about"},
            body_class="public-page-about",
        )
    )


@router.get("/contact", response_class=HTMLResponse)
async def public_contact() -> HTMLResponse:
    async with async_session() as session:
        profile = await get_public_profile(session)
    payload = _profile_payload(profile)
    contact_items = list(payload.get("contact") or payload.get("contact_modes") or [])
    hero_media = _photo_block((payload.get("photos") or {}).get("contact"), class_name="public-hero-visual", label="Contact Portrait")
    content = f"""
    <section class="public-editorial-band">
      <article class="public-story-card public-story-card--wide">
        <div class="public-kicker">Collaboration Modes</div>
        <h2>Clean lanes for serious conversations.</h2>
        <p>Best fits include high-ownership engineering roles, AI-native product work, and selective freelance collaborations where the product and systems challenge are both real.</p>
        {_string_list([
            "Hiring conversations for backend, platform, AI, or product engineering roles.",
            "Freelance work where design, engineering, deployment, and maintenance matter together.",
            "Peer conversations about agents, memory systems, data products, or operations."
        ])}
      </article>
      <article class="public-story-card">
        <div class="public-kicker">Base</div>
        <h2>{_safe(payload.get("location") or settings.public_profile_location)}</h2>
        <p>{_safe(payload.get("professional_summary") or profile.get("summary") or "")}</p>
      </article>
    </section>
    <section class="public-section">
      <div class="public-section-heading">
        <div class="public-kicker">Channels</div>
        <h2>Public-facing contact only.</h2>
      </div>
      <div class="public-contact-grid">{_contact_rows(contact_items)}</div>
    </section>
    """
    return HTMLResponse(
        render_public_shell(
            page_title=f"Contact {settings.public_profile_short_name}",
            hero_kicker="Contact",
            hero_title="Reach Ahmad without digging through the private brain.",
            hero_subtitle="Selective public channels for collaboration, hiring, and direct follow-up.",
            hero_media_html=hero_media,
            content_html=content,
            active_nav="contact",
            page_data={"page": "contact"},
            body_class="public-page-contact",
        )
    )


@router.get("/projects", response_class=HTMLResponse)
async def public_projects() -> HTMLResponse:
    async with async_session() as session:
        profile = await get_public_profile(session)
        projects = await list_public_projects(session)
    payload = _profile_payload(profile)
    hero_media = _photo_block((payload.get("photos") or {}).get("work"), class_name="public-hero-visual", label="Work Portrait")
    content = f"""
    <section class="public-section">
      <div class="public-section-heading">
        <div class="public-kicker">Case Studies</div>
        <h2>Products, client work, and systems that carry proof.</h2>
      </div>
      <div class="public-case-grid public-case-grid--full">
        {''.join(_project_card(project, detail=True) for project in projects)}
      </div>
    </section>
    <section class="public-section">
      <div class="public-section-heading">
        <div class="public-kicker">Expertise Books</div>
        <h2>The domains these projects roll up into.</h2>
      </div>
      <div class="public-capability-grid">{_capability_cards(list(payload.get("capabilities") or []))}</div>
    </section>
    """
    return HTMLResponse(
        render_public_shell(
            page_title=f"{settings.public_profile_short_name} Projects",
            hero_kicker="Projects",
            hero_title="A body of work built around ownership, systems depth, and product conviction.",
            hero_subtitle="Each project is framed as proof: what problem it came from, how it was built, and what it demonstrates.",
            hero_media_html=hero_media,
            content_html=content,
            active_nav="projects",
            page_data={"page": "projects"},
            body_class="public-page-projects",
        )
    )


@router.get("/projects/{slug}", response_class=HTMLResponse)
async def public_project_detail(slug: str) -> HTMLResponse:
    async with async_session() as session:
        profile = await get_public_profile(session)
        project = await get_public_project(session, slug)
    if not project:
        raise HTTPException(status_code=404, detail="Public project not found")
    payload = dict(project.get("payload") or {})
    profile_payload = _profile_payload(profile)
    hero_media = _photo_block((profile_payload.get("photos") or {}).get("work"), class_name="public-hero-visual", label="Project Context")
    signals = list(payload.get("signals") or [])
    content = f"""
    <section class="public-editorial-band">
      <article class="public-story-card public-story-card--wide">
        <div class="public-kicker">{_safe(payload.get("status") or "Project")}</div>
        <h2>{_safe(payload.get("tagline") or project.get("summary") or "")}</h2>
        <p>{_safe(payload.get("summary") or project.get("summary") or "")}</p>
        {_pill_list(list(payload.get("stack") or [])[:8])}
      </article>
      <article class="public-story-card">
        <div class="public-kicker">Live Links</div>
        <div class="public-link-column">
          {''.join(f'<a class="public-inline-link" href="{_safe(item.get("href"))}" target="_blank" rel="noreferrer">{_safe(item.get("label") or "Open")}</a>' for item in list(payload.get("links") or []) if item.get("href")) or '<span>No public links yet.</span>'}
        </div>
      </article>
    </section>
    <section class="public-editorial-band">
      <article class="public-story-card">
        <div class="public-kicker">How It Was Framed</div>
        {_string_list(list(payload.get("resume_bullets") or [])[:5])}
      </article>
      <article class="public-story-card">
        <div class="public-kicker">What It Demonstrates</div>
        {_string_list(list(payload.get("demonstrates") or [])[:5])}
      </article>
    </section>
    <section class="public-section">
      <div class="public-section-heading">
        <div class="public-kicker">Signals</div>
        <h2>Approved narrative and live status fragments.</h2>
      </div>
      <div class="public-signal-grid">
        {''.join(
            f'''
            <article class="public-signal-card">
              <div class="public-kicker">{_safe(item.get("fact_type") or "signal")}</div>
              <strong>{_safe(item.get("title") or project.get("title"))}</strong>
              <p>{_safe(item.get("body") or "")}</p>
              <span>{_safe(item.get("updated_at") or "")}</span>
            </article>
            '''
            for item in signals
        ) or '<article class="public-signal-card"><strong>No separate signal cards yet.</strong></article>'}
      </div>
    </section>
    """
    return HTMLResponse(
        render_public_shell(
            page_title=_safe(project["title"]),
            hero_kicker="Project",
            hero_title=_safe(project["title"]),
            hero_subtitle=_safe(payload.get("summary") or project.get("summary") or ""),
            hero_media_html=hero_media,
            content_html=content,
            active_nav="projects",
            page_data={"page": "project-detail", "project": project},
            body_class="public-page-project-detail",
        )
    )


@router.get("/open-brain", response_class=HTMLResponse)
async def public_open_brain() -> HTMLResponse:
    async with async_session() as session:
        profile = await get_public_profile(session)
        projects = await list_public_projects(session)
        faq = await list_public_faq(session)
        policy = await get_public_answer_policy(session)
    payload = _profile_payload(profile)
    hero_media = _photo_block((payload.get("photos") or {}).get("personality"), class_name="public-hero-visual", label="Open Brain Portrait")
    faq_html = "".join(
        f"""
        <article class="public-proof-card">
          <div class="public-kicker">FAQ</div>
          <h3>{_safe(item["question"])}</h3>
          <p>{_safe(item["answer"])}</p>
        </article>
        """
        for item in faq
    )
    turnstile_configured = bool(settings.cloudflare_turnstile_site_key and settings.cloudflare_turnstile_secret_key)
    content = f"""
    <section class="public-editorial-band">
      <article class="public-story-card public-story-card--wide">
        <div class="public-kicker">Approved Scope</div>
        <h2>The public brain answers from approved narrative only.</h2>
        <p>{_safe(policy.get("summary") or "The public surface is curated and intentionally narrow.")}</p>
        {_render_public_recent_signals(projects)}
      </article>
      <article class="public-story-card">
        <div class="public-kicker">Thought Garden</div>
        {_pill_list([item.get("title") or "" for item in list(payload.get("thought_garden") or [])[:6]])}
      </article>
    </section>
    <section class="public-chat-shell">
      <div class="public-chat-log" data-public-chat-log>
        <div class="public-chat-message">
          <strong>Open Brain</strong>
          <div>Ask about Ahmad's work, projects, strengths, interests, or collaboration fit. This is not a general-purpose assistant.</div>
        </div>
      </div>
      <form class="public-chat-form" data-public-chat-form>
        <textarea name="question" placeholder="What kind of engineer is Ahmad? What is duSraBheja? Would he be a strong collaborator on AI infrastructure?"></textarea>
        <input type="hidden" name="turnstile_token" value="" />
        <div id="turnstile-widget"></div>
        <button class="public-cta" type="submit" {'disabled' if not turnstile_configured else ''}>Ask the open brain</button>
        <div class="public-chat-footnote" data-public-chat-status>
          {'Public chat is limited to Ahmad/profile/project questions.' if turnstile_configured else 'Turnstile isn’t configured yet, so public chat is locked for now.'}
        </div>
      </form>
    </section>
    <section class="public-section">
      <div class="public-section-heading">
        <div class="public-kicker">FAQ</div>
        <h2>Common public questions.</h2>
      </div>
      <div class="public-proof-grid">{faq_html}</div>
    </section>
    """
    page_script = ""
    if turnstile_configured:
        page_script = f"""
        window.addEventListener('load', function () {{
          const renderWidget = function () {{
            if (!window.turnstile) return;
            window.turnstile.render('#turnstile-widget', {{
              sitekey: {settings.cloudflare_turnstile_site_key!r},
              callback: function (token) {{
                const field = document.querySelector('input[name="turnstile_token"]');
                if (field) field.value = token;
              }},
            }});
          }};
          renderWidget();
        }});
        """
        content = '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>' + content
    return HTMLResponse(
        render_public_shell(
            page_title=f"Open Brain — {settings.public_profile_short_name}",
            hero_kicker="Open Brain",
            hero_title="Ask the public-facing brain.",
            hero_subtitle="A curated profile bot for collaborators, recruiters, and curious humans. It only answers from approved public narrative and approved public facts.",
            hero_media_html=hero_media,
            content_html=content,
            active_nav="open-brain",
            page_data={"page": "open-brain", "turnstileConfigured": turnstile_configured},
            page_script=page_script,
            body_class="public-page-open-brain",
        )
    )


@router.get("/admin", response_class=HTMLResponse)
async def public_admin_redirect() -> RedirectResponse:
    return RedirectResponse(url="/dashboard/login", status_code=303)


@router.get("/api/public/profile")
async def public_profile_api() -> dict:
    async with async_session() as session:
        return await get_public_profile(session)


@router.get("/api/public/projects")
async def public_projects_api() -> dict:
    async with async_session() as session:
        projects = await list_public_projects(session)
    return {"count": len(projects), "items": projects}


@router.get("/api/public/faq")
async def public_faq_api() -> dict:
    async with async_session() as session:
        faq = await list_public_faq(session)
    return {"count": len(faq), "items": faq}


@router.post("/api/public/chat")
async def public_chat_api(request: Request, payload: PublicChatRequest) -> dict:
    async with async_session() as session:
        result = await answer_public_question(
            session,
            question=payload.question,
            remote_ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            turnstile_token=payload.turnstile_token,
        )
    if not result.get("ok"):
        raise HTTPException(status_code=int(result.get("status_code") or 400), detail=result.get("detail") or "Public chat failed.")
    return result


@router.get("/api/public/health")
async def public_health_api() -> dict:
    return {
        "status": "ok",
        "site_title": settings.public_site_title,
        "chat_enabled": bool(settings.cloudflare_turnstile_site_key and settings.cloudflare_turnstile_secret_key),
    }
