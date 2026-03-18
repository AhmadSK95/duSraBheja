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
    answer_public_chat,
    get_public_answer_policy,
    get_public_profile,
    get_public_project,
    list_public_faq,
    list_public_projects,
)

router = APIRouter(tags=["public"])


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _payload(profile: dict) -> dict:
    return dict(profile.get("payload") or {})


def _s(value: object | None) -> str:
    return html.escape(str(value or ""))


def _short_name(payload: dict) -> str:
    return str(
        payload.get("short_name")
        or payload.get("preferred_name")
        or settings.public_profile_short_name
    )


def _photo_url(photo: dict | None) -> str:
    if not photo or not photo.get("url"):
        return ""
    return _s(photo["url"])


def _photo_img(photo: dict | None, *, loading: str = "lazy", alt: str = "") -> str:
    url = _photo_url(photo)
    if not url:
        return ""
    return f'<img src="{url}" alt="{_s(alt or photo.get("title", ""))}" loading="{loading}" />'


def _pills(items: list[str], *, cls: str = "pill-list") -> str:
    tags = "".join(f'<span class="pill">{_s(item)}</span>' for item in items if item)
    return f'<div class="{cls}">{tags}</div>' if tags else ""


def _numbered_list(items: list[str]) -> str:
    rows = "".join(f"<li>{_s(item)}</li>" for item in items if item)
    return f'<ol class="numbered-list">{rows}</ol>' if rows else ""


def _bullet_list(items: list[str]) -> str:
    rows = "".join(f"<li>{_s(item)}</li>" for item in items if item)
    return f'<ul class="public-bullet-list">{rows}</ul>' if rows else ""


def _project_card_html(project: dict, *, featured: bool = False) -> str:
    p = dict(project.get("payload") or {})
    stack = p.get("stack") or []
    slug = project.get("slug") or ""
    featured_cls = " project-card--featured" if featured else ""
    links_html = "".join(
        f'<a class="inline-link" href="{_s(item.get("href"))}"'
        f' target="_blank" rel="noreferrer">'
        f'{_s(item.get("label") or "Open")}</a>'
        for item in (p.get("links") or [])
        if item.get("href")
    )
    return f"""
    <article class="project-card{featured_cls} reveal">
      <div class="public-kicker">{_s(p.get("status") or "Case Study")}</div>
      <h3>{_s(project.get("title"))}</h3>
      <p>{_s(p.get("tagline") or project.get("summary") or "")}</p>
      {_pills(stack[:6])}
      <div class="link-row mt-2">
        <a class="inline-link" href="/projects/{_s(slug)}">Read case study</a>
        {links_html}
      </div>
    </article>
    """


# ──────────────────────────────────────────────
# Asset route
# ──────────────────────────────────────────────

@router.get("/public-assets/profile/{filename}")
async def public_profile_asset(filename: str) -> FileResponse:
    path = public_asset_path(filename)
    if not path:
        raise HTTPException(status_code=404, detail="Public asset not found")
    return FileResponse(path)


# ──────────────────────────────────────────────
# Home
# ──────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def public_home() -> HTMLResponse:
    async with async_session() as session:
        profile = await get_public_profile(session)
        projects = await list_public_projects(session)
    p = _payload(profile)
    name = _short_name(p)
    photos = p.get("photos") or {}
    hero_photo = photos.get("hero")
    personality_photo = photos.get("personality")
    photo_break = photos.get("photo_break")
    current_arc = p.get("current_arc") or {}
    # 1. Full-viewport hero
    hero_html = f"""
    <section class="hero-full">
      <div class="hero-full__bg">
        {_photo_img(hero_photo, loading="eager", alt=f"{name} portrait")}
      </div>
      <div class="hero-full__content">
        <div class="public-kicker">Living Profile</div>
        <h1 class="display-heading display-heading--hero">{_s(name)}</h1>
        <p>{_s(p.get("hero_summary") or profile.get("summary") or "")}</p>
      </div>
      <div class="hero-full__scroll">scroll</div>
    </section>
    """

    # 2. Statement band (dark bg with metrics)
    stat_html = f"""
    <section class="full-bleed dark-band reveal">
      <div class="container">
        <div class="stat-band">
          <div class="stat-item">
            <div class="stat-item__number">6+</div>
            <div class="stat-item__label">Years Engineering</div>
          </div>
          <div class="stat-item">
            <div class="stat-item__number">{len(projects)}</div>
            <div class="stat-item__label">Shipped Products</div>
          </div>
          <div class="stat-item">
            <div class="stat-item__number">3</div>
            <div class="stat-item__label">Countries Worked</div>
          </div>
          <div class="stat-item">
            <div class="stat-item__number">1</div>
            <div class="stat-item__label">AI Second Brain</div>
          </div>
        </div>
      </div>
    </section>
    """

    # 3. About teaser (60/40 asymmetric)
    about_html = f"""
    <section class="section container reveal">
      <div class="offset-grid offset-grid--60-40">
        <div>
          <div class="public-kicker">About</div>
          <h2 class="display-heading display-heading--section">
            Builder, engineer, restless generalist.</h2>
          <p>{_s(p.get("hero_summary") or profile.get("summary") or "")}</p>
          <a class="inline-link mt-2" href="/about">Read the full story</a>
        </div>
        <div class="hero-inner__photo">
          {_photo_img(personality_photo, alt="Ahmad with Oscar")}
        </div>
      </div>
    </section>
    """

    # 4. Selected work (staggered)
    first_project = _project_card_html(projects[0], featured=True) if projects else ""
    grid_projects = "".join(_project_card_html(proj) for proj in projects[1:4])
    work_html = f"""
    <section class="section container reveal">
      <div class="public-kicker">Selected Work</div>
      <h2 class="display-heading display-heading--section">
        Proof across AI systems, product thinking,
        and real delivery.</h2>
      {first_project}
      <div class="project-grid mt-3">
        {grid_projects}
        <article class="project-card reveal"
          style="display:flex;align-items:center;
          justify-content:center;">
          <a class="cta" href="/projects">View all projects</a>
        </article>
      </div>
    </section>
    """

    # 5. Photo break
    photo_break_html = ""
    if photo_break and photo_break.get("url"):
        photo_break_html = f"""
        <section class="full-bleed photo-break reveal">
          <img src="{_s(photo_break['url'])}" alt="NYC skyline" loading="lazy" />
          <div class="photo-break__overlay">
            <p class="photo-break__quote">Build things that remember. Ship things that matter.</p>
          </div>
        </section>
        """

    # 6. Current arc
    focus_items = list(current_arc.get("focus") or [])[:5]
    arc_html = f"""
    <section class="section container container--narrow text-center reveal">
      <div class="public-kicker">Current Arc</div>
      <h2 class="display-heading display-heading--section">
        {_s(current_arc.get("title") or "What is happening now")}
      </h2>
      <p>{_s(current_arc.get("summary") or "")}</p>
      {_numbered_list(focus_items)}
    </section>
    """

    content = hero_html + stat_html + about_html + work_html + photo_break_html + arc_html
    return HTMLResponse(
        render_public_shell(
            page_title=f"{name} — Builder, Engineer, Restless Generalist",
            content_html=content,
            active_nav="home",
            page_data={"page": "home"},
            body_class="public-page-home",
            og_description=(p.get("hero_summary") or "").strip(),
        )
    )


# ──────────────────────────────────────────────
# About
# ──────────────────────────────────────────────

@router.get("/about", response_class=HTMLResponse)
async def public_about() -> HTMLResponse:
    async with async_session() as session:
        profile = await get_public_profile(session)
    p = _payload(profile)
    name = _short_name(p)
    photos = p.get("photos") or {}
    personality_photo = photos.get("personality")
    mosaic_photos = [item for item in (photos.get("mosaic") or []) if item and item.get("url")]
    eras = list(p.get("eras") or [])
    roles = list(p.get("roles") or [])
    current_arc = p.get("current_arc") or {}

    # 1. Hero
    hero_html = f"""
    <section class="hero-inner">
      <div class="container">
        <div>
          <div class="public-kicker">About</div>
          <h1 class="display-heading display-heading--hero">
            From IIT Kharagpur to New York
            to independent builder.</h1>
          <p>{_s(current_arc.get("summary") or profile.get("summary") or "")}</p>
        </div>
      </div>
    </section>
    """

    # 2. Origin statement
    origin_html = f"""
    <section class="section container container--narrow reveal">
      <div class="offset-grid offset-grid--60-40">
        <div>
          <div class="public-kicker">Origin</div>
          <h2 class="display-heading display-heading--sub">
            A career built by moving into harder rooms
            and shipping real things.</h2>
          <p>{_s(p.get("hero_summary") or profile.get("summary") or "")}</p>
        </div>
        <div class="hero-inner__photo">
          {_photo_img(personality_photo, alt="Ahmad with Oscar")}
        </div>
      </div>
    </section>
    """

    # 3. Timeline (horizontal scroll)
    timeline_panels = "".join(
        f"""
        <div class="timeline-panel">
          <div class="public-kicker">{_s(era.get("years"))}</div>
          <h3>{_s(era.get("title"))}</h3>
          <p>{_s(era.get("summary"))}</p>
          {_bullet_list(list(era.get("highlights") or [])[:4])}
        </div>
        """
        for era in eras
    )
    timeline_html = f"""
    <section class="section container reveal">
      <div class="public-kicker">Chapters</div>
      <h2 class="display-heading display-heading--section">
        The chapters that shape the whole picture.</h2>
      <div class="timeline-scroll">
        {timeline_panels}
      </div>
    </section>
    """

    # 4. Photo mosaic
    mosaic_html = ""
    if mosaic_photos:
        mosaic_imgs = "".join(
            f'<img src="{_s(item["url"])}" alt="{_s(item.get("title", ""))}" loading="lazy" />'
            for item in mosaic_photos[:5]
        )
        mosaic_html = f"""
        <section class="section container reveal">
          <div class="photo-mosaic">{mosaic_imgs}</div>
        </section>
        """

    # 5. Roles (compact table)
    role_rows = "".join(
        f"""
        <div class="role-row">
          <span class="role-row__period">{_s(item.get("period"))}</span>
          <span class="role-row__org">{_s(item.get("organization"))}</span>
          <span class="role-row__title">{_s(item.get("title"))}</span>
          <span class="role-row__summary">{_s(item.get("summary"))}</span>
        </div>
        """
        for item in roles[:6]
    )
    roles_html = f"""
    <section class="section container reveal">
      <div class="public-kicker">Career</div>
      <h2 class="display-heading display-heading--section">Career proof, condensed.</h2>
      <div class="roles-table">{role_rows}</div>
    </section>
    """

    content = hero_html + origin_html + timeline_html + mosaic_html + roles_html
    return HTMLResponse(
        render_public_shell(
            page_title=f"About {name}",
            content_html=content,
            active_nav="about",
            page_data={"page": "about"},
            body_class="public-page-about",
            og_description="From IIT Kharagpur to New York to independent builder.",
        )
    )


# ──────────────────────────────────────────────
# Projects
# ──────────────────────────────────────────────

@router.get("/projects", response_class=HTMLResponse)
async def public_projects() -> HTMLResponse:
    async with async_session() as session:
        profile = await get_public_profile(session)
        projects = await list_public_projects(session)
    p = _payload(profile)
    name = _short_name(p)
    photos = p.get("photos") or {}
    capabilities = list(p.get("capabilities") or [])

    # Hero
    hero_html = f"""
    <section class="hero-inner">
      <div class="container">
        <div>
          <div class="public-kicker">Projects</div>
          <h1 class="display-heading display-heading--hero">Work.</h1>
          <p>Each project is framed as proof: what problem
            it came from, how it was built,
            and what it demonstrates.</p>
        </div>
        <div class="hero-inner__photo">
          {_photo_img(photos.get("work"), alt="Ahmad street portrait")}
        </div>
      </div>
    </section>
    """

    # Featured + grid
    featured = _project_card_html(projects[0], featured=True) if projects else ""
    grid = "".join(_project_card_html(proj) for proj in projects[1:])
    projects_html = f"""
    <section class="section container">
      {featured}
      <div class="project-grid mt-3">
        {grid}
      </div>
    </section>
    """

    # Capabilities (horizontal scroll tags)
    cap_tags = "".join(
        f'<span class="capability-tag">{_s(item.get("title"))}</span>'
        for item in capabilities[:8]
    )
    cap_html = f"""
    <section class="section container reveal">
      <div class="public-kicker">Domains</div>
      <h2 class="display-heading display-heading--sub">The domains these projects roll up into.</h2>
      <div class="capability-scroll">{cap_tags}</div>
    </section>
    """

    content = hero_html + projects_html + cap_html
    return HTMLResponse(
        render_public_shell(
            page_title=f"{name} Projects",
            content_html=content,
            active_nav="projects",
            page_data={"page": "projects"},
            body_class="public-page-projects",
            og_description=(
                "A body of work built around ownership,"
                " systems depth, and product conviction."
            ),
        )
    )


# ──────────────────────────────────────────────
# Project Detail
# ──────────────────────────────────────────────

@router.get("/projects/{slug}", response_class=HTMLResponse)
async def public_project_detail(slug: str) -> HTMLResponse:
    async with async_session() as session:
        await get_public_profile(session)
        project = await get_public_project(session, slug)
    if not project:
        raise HTTPException(status_code=404, detail="Public project not found")
    proj_p = dict(project.get("payload") or {})
    signals = list(proj_p.get("signals") or [])

    # Hero
    hero_html = f"""
    <section class="hero-inner">
      <div class="container">
        <div>
          <div class="public-kicker">Project</div>
          <h1 class="display-heading display-heading--section">{_s(project["title"])}</h1>
          <span class="status-badge">{_s(proj_p.get("status") or "Active")}</span>
          <p class="mt-2">{_s(proj_p.get("tagline") or project.get("summary") or "")}</p>
        </div>
      </div>
    </section>
    """

    # Two-column layout
    stack_pills = _pills(list(proj_p.get("stack") or [])[:8])
    links_html = "".join(
        f'<a class="inline-link" href="{_s(item.get("href"))}"'
        f' target="_blank" rel="noreferrer">'
        f'{_s(item.get("label") or "Open")}</a>'
        for item in list(proj_p.get("links") or [])
        if item.get("href")
    ) or "<span class='mono-accent'>No public links yet.</span>"

    demonstrates_html = _bullet_list(list(proj_p.get("demonstrates") or [])[:5])
    summary_html = _s(proj_p.get("summary") or project.get("summary") or "")
    framing_html = _bullet_list(list(proj_p.get("resume_bullets") or [])[:5])

    # Signal feed
    signal_items = "".join(
        f"""
        <div class="signal-item">
          <span class="signal-item__time">{_s(item.get("updated_at") or "")}</span>
          <div class="signal-item__body">
            <strong>{_s(item.get("title") or project.get("title"))}</strong>
            <p>{_s(item.get("body") or "")}</p>
          </div>
        </div>
        """
        for item in signals
    ) or '<p class="mono-accent">No signal entries yet.</p>'

    detail_html = f"""
    <section class="section container">
      <div class="detail-layout">
        <div>
          <div class="public-kicker">Overview</div>
          <p>{summary_html}</p>
          <div class="mt-3">
            <div class="public-kicker">How It Was Framed</div>
            {framing_html}
          </div>
          <div class="mt-3">
            <div class="public-kicker">Signals</div>
            <div class="signal-feed">{signal_items}</div>
          </div>
        </div>
        <div class="detail-sidebar">
          <div class="detail-sidebar__block">
            <h4>Stack</h4>
            {stack_pills}
          </div>
          <div class="detail-sidebar__block">
            <h4>Links</h4>
            <div class="link-column">{links_html}</div>
          </div>
          <div class="detail-sidebar__block">
            <h4>Demonstrates</h4>
            {demonstrates_html}
          </div>
        </div>
      </div>
    </section>
    """

    content = hero_html + detail_html
    return HTMLResponse(
        render_public_shell(
            page_title=_s(project["title"]),
            content_html=content,
            active_nav="projects",
            page_data={"page": "project-detail", "project": project},
            body_class="public-page-project-detail",
            og_description=_s(proj_p.get("tagline") or project.get("summary") or ""),
        )
    )


# ──────────────────────────────────────────────
# Contact
# ──────────────────────────────────────────────

@router.get("/contact", response_class=HTMLResponse)
async def public_contact() -> HTMLResponse:
    async with async_session() as session:
        profile = await get_public_profile(session)
    p = _payload(profile)
    name = _short_name(p)
    photos = p.get("photos") or {}
    contact_items = list(p.get("contact") or p.get("contact_modes") or [])

    # Hero
    hero_html = f"""
    <section class="hero-inner">
      <div class="container">
        <div>
          <div class="public-kicker">Contact</div>
          <h1 class="display-heading display-heading--hero">Let's talk.</h1>
          <p>Selective public channels for collaboration, hiring, and direct follow-up.</p>
        </div>
        <div class="hero-inner__photo">
          {_photo_img(photos.get("contact"), alt="Ahmad with Oscar")}
        </div>
      </div>
    </section>
    """

    # Contact rows
    rows_html = ""
    for item in contact_items:
        href = item.get("href")
        if not href:
            continue
        rows_html += f"""
        <div class="contact-row">
          <span class="contact-row__label">{_s(item.get("label") or "Contact")}</span>
          <span class="contact-row__value">{_s(item.get("value") or item.get("label"))}</span>
          <span class="contact-row__action">
            <a href="{_s(href)}"
              target="_blank" rel="noreferrer">Open</a>
          </span>
        </div>
        """

    location = p.get("location") or settings.public_profile_location
    contact_html = f"""
    <section class="section container reveal">
      <div class="public-kicker">Channels</div>
      <h2 class="display-heading display-heading--sub">Public-facing contact only.</h2>
      <div class="contact-rows">{rows_html}</div>
      <p class="mt-4" style="font-size:1.25rem;font-weight:600;">{_s(location)}</p>
    </section>
    """

    content = hero_html + contact_html
    return HTMLResponse(
        render_public_shell(
            page_title=f"Contact {name}",
            content_html=content,
            active_nav="contact",
            page_data={"page": "contact"},
            body_class="public-page-contact",
            og_description=(
                "Selective public channels for"
                " collaboration, hiring, and direct follow-up."
            ),
        )
    )


# ──────────────────────────────────────────────
# Open Brain
# ──────────────────────────────────────────────

@router.get("/open-brain", response_class=HTMLResponse)
async def public_open_brain() -> HTMLResponse:
    async with async_session() as session:
        profile = await get_public_profile(session)
        faq = await list_public_faq(session)
        await get_public_answer_policy(session)
    p = _payload(profile)
    name = _short_name(p)
    thought_garden = list(p.get("thought_garden") or [])

    turnstile_configured = bool(
        settings.cloudflare_turnstile_site_key and settings.cloudflare_turnstile_secret_key
    )

    # Chat shell (promoted above fold, terminal aesthetic)
    turnstile_widget = ""
    if turnstile_configured:
        turnstile_widget = '<div id="turnstile-widget"></div>'

    chat_html = f"""
    <section class="section container">
      <div class="offset-grid offset-grid--65-35">
        <div>
          <div class="public-kicker">Digital Clone</div>
          <h1 class="display-heading display-heading--section">Ask Ahmad's brain.</h1>
          <p>A conversational clone built from approved
            public facts, project history, and persona.
            Multi-turn, evidence-led, opinionated.</p>
        </div>
        <div></div>
      </div>
      <div class="chat-shell mt-3">
        <div class="chat-shell__header">
          <span class="chat-shell__dot"></span>
          <span class="chat-shell__title">open-brain &mdash; {_s(name)}</span>
          <button class="chat-shell__new-btn" data-new-conversation>New conversation</button>
        </div>
        <div class="chat-log" data-public-chat-log>
          <div class="chat-message">
            <strong>Ahmad's Clone</strong>
            <div>Ask about my work, projects, strengths,
              interests, or collaboration fit.
              I'm not a general-purpose assistant
              — I'm a conversational version of Ahmad,
              built from real evidence.</div>
          </div>
        </div>
        <form class="chat-form" data-public-chat-form>
          <textarea name="question"
            placeholder="What kind of engineer is Ahmad?
            What is duSraBheja?
            Would he be a strong fit for an AI
            infrastructure role?"></textarea>
          <input type="hidden" name="turnstile_token" value="" />
          {turnstile_widget}
          <button class="cta" type="submit"
            {'disabled' if not turnstile_configured else ''}
          >Ask the clone</button>
          <div class="chat-footnote"
            data-public-chat-status>
            {'Multi-turn conversation. Ask follow-ups.'
             if turnstile_configured
             else "Turnstile isn't configured yet,"
                  " so chat is locked."}
          </div>
        </form>
      </div>
    </section>
    """

    # FAQ accordion
    faq_items = "".join(
        f"""
        <details class="faq-item">
          <summary>{_s(item["question"])}</summary>
          <div class="faq-answer">{_s(item["answer"])}</div>
        </details>
        """
        for item in faq
    )
    faq_html = f"""
    <section class="section container reveal">
      <div class="public-kicker">FAQ</div>
      <h2 class="display-heading display-heading--sub">Common questions.</h2>
      <div class="faq-list">{faq_items}</div>
    </section>
    """

    # Thought garden
    garden_tags = "".join(
        f'<span class="thought-tag">{_s(item.get("title") or "")}</span>'
        for item in thought_garden[:8]
    )
    garden_html = f"""
    <section class="section container reveal">
      <div class="public-kicker">Thought Garden</div>
      <div class="thought-garden">{garden_tags}</div>
    </section>
    """ if garden_tags else ""

    page_script = ""
    if turnstile_configured:
        page_script = f"""
        window.addEventListener('load', function () {{
          if (!window.turnstile) return;
          window.turnstile.render('#turnstile-widget', {{
            sitekey: {settings.cloudflare_turnstile_site_key!r},
            callback: function (token) {{
              var field = document.querySelector('input[name="turnstile_token"]');
              if (field) field.value = token;
            }},
          }});
        }});
        """
        turnstile_tag = (
            '<script src="https://challenges.cloudflare.com'
            '/turnstile/v0/api.js" async defer></script>'
        )
        chat_html = turnstile_tag + chat_html

    content = chat_html + faq_html + garden_html
    return HTMLResponse(
        render_public_shell(
            page_title=f"Open Brain — {name}",
            content_html=content,
            active_nav="open-brain",
            page_data={"page": "open-brain", "turnstileConfigured": turnstile_configured},
            page_script=page_script,
            body_class="public-page-open-brain",
            og_description=(
                "A conversational digital clone for"
                " collaborators, recruiters,"
                " and curious humans."
            ),
        )
    )


# ──────────────────────────────────────────────
# Admin redirect
# ──────────────────────────────────────────────

@router.get("/admin", response_class=HTMLResponse)
async def public_admin_redirect() -> RedirectResponse:
    return RedirectResponse(url="/dashboard/login", status_code=303)


# ──────────────────────────────────────────────
# JSON APIs
# ──────────────────────────────────────────────

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
        result = await answer_public_chat(
            session,
            question=payload.question,
            remote_ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            turnstile_token=payload.turnstile_token,
            conversation_id=payload.conversation_id,
        )
    if not result.get("ok"):
        raise HTTPException(
            status_code=int(result.get("status_code") or 400),
            detail=result.get("detail") or "Public chat failed.",
        )
    return result


@router.get("/api/public/health")
async def public_health_api() -> dict:
    return {
        "status": "ok",
        "site_title": settings.public_site_title,
        "chat_enabled": bool(
            settings.cloudflare_turnstile_site_key and settings.cloudflare_turnstile_secret_key
        ),
    }
