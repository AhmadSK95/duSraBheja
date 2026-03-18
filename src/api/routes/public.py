"""Public-facing site and chatbot routes.

Pages read sections from the DB (WebsiteSection) when available,
falling back to seed-data-driven hardcoded layouts.
"""

from __future__ import annotations

import html
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from src.api.public_ui import render_public_shell
from src.api.schemas import PublicChatRequest
from src.config import settings
from src.database import async_session
from src.models import WebsiteSection
from src.services.profile_narrative import public_asset_path
from src.services.public_surface import (
    answer_public_chat,
    get_public_answer_policy,
    get_public_profile,
    get_public_project,
    list_public_faq,
    list_public_projects,
)
from src.services.website import list_page_sections

router = APIRouter(tags=["public"])
log = logging.getLogger("brain.public")


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


def _photo_img_sticker(photos: dict, key: str | None, tilt: str | None = None) -> str:
    """Render a photo as a sticker with optional tilt class."""
    if not key:
        return ""
    photo = photos.get(key)
    if not photo:
        return ""
    tilt_cls = f" photo-sticker--tilt-{tilt}" if tilt else ""
    url = _photo_url(photo)
    if not url:
        return ""
    return (
        f'<div class="photo-sticker{tilt_cls}">'
        f'<img src="{url}" alt="{_s(photo.get("title", ""))}" loading="lazy" />'
        f"</div>"
    )


def _pills(items: list[str], *, cls: str = "pill-list") -> str:
    tags = "".join(f'<span class="pill">{_s(item)}</span>' for item in items if item)
    return f'<div class="{cls}">{tags}</div>' if tags else ""


def _numbered_list(items: list[str]) -> str:
    rows = "".join(f"<li>{_s(item)}</li>" for item in items if item)
    return f'<ol class="numbered-list">{rows}</ol>' if rows else ""


def _bullet_list(items: list[str]) -> str:
    rows = "".join(f"<li>{_s(item)}</li>" for item in items if item)
    return f'<ul class="public-bullet-list">{rows}</ul>' if rows else ""


def _kicker(text: str | None) -> str:
    if not text:
        return ""
    return f'<div class="public-kicker">{_s(text)}</div>'


def _section_heading(text: str | None) -> str:
    if not text:
        return ""
    t = _s(text)
    return f'<h2 class="display-heading display-heading--section">{t}</h2>'


def _card_html(cls: str, title: str, body: str) -> str:
    return f'<div class="{cls}"><h4>{_s(title)}</h4><p>{_s(body)}</p></div>'


def _project_card_html(project: dict, *, featured: bool = False) -> str:
    p = dict(project.get("payload") or {})
    stack = p.get("stack") or []
    slug = project.get("slug") or ""
    featured_cls = " project-card--featured" if featured else ""
    links_html = "".join(
        f'<a class="inline-link" href="{_s(item.get("href"))}"'
        f' target="_blank" rel="noreferrer">'
        f"{_s(item.get('label') or 'Open')}</a>"
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
# Generic section renderer
# ──────────────────────────────────────────────


def _render_section(
    section: WebsiteSection,
    photos: dict,
    projects: list,
) -> str:
    """Render any WebsiteSection to HTML based on its type."""
    c = section.content or {}
    # style_hints available via section.style_hints for future use

    if section.section_type == "hero":
        photo_html = _photo_img_sticker(photos, c.get("photo_key"), c.get("sticker_tilt"))
        cta_primary = c.get("cta_primary") or {}
        cta_secondary = c.get("cta_secondary") or {}
        ctas = ""
        if cta_primary.get("label"):
            href = _s(cta_primary.get("href", "/"))
            label = _s(cta_primary["label"])
            ctas += f'<a class="cta" href="{href}">{label}</a>'
        if cta_secondary.get("label"):
            href = _s(cta_secondary.get("href", "/"))
            label = _s(cta_secondary["label"])
            ctas += f'<a class="cta cta--outline" href="{href}">{label}</a>'
        return f"""
        <section class="hero-home">
          <div class="container">
            <div class="hero-home__text">
              <h1 class="display-heading display-heading--hero">{_s(c.get("heading"))}</h1>
              <p>{_s(c.get("subline"))}</p>
              <div class="hero-home__ctas">{ctas}</div>
            </div>
            {photo_html}
          </div>
        </section>
        """

    elif section.section_type == "text_block":
        kckr = _kicker(c.get("kicker"))
        hdng = _section_heading(c.get("heading"))
        body = f"<p>{_s(c.get('body'))}</p>" if c.get("body") else ""
        photo_html = _photo_img_sticker(photos, c.get("photo_key"), c.get("sticker_tilt"))
        link = c.get("link") or {}
        link_html = ""
        if link.get("label"):
            lref = _s(link.get("href"))
            ltxt = _s(link.get("label"))
            link_html = f'<a class="inline-link mt-2" href="{lref}">{ltxt}</a>'
        if photo_html:
            return f"""
            <section class="section container reveal">
              <div class="offset-grid offset-grid--60-40">
                <div>{kckr}{hdng}{body}{link_html}</div>
                <div class="photo-accent--md">{photo_html}</div>
              </div>
            </section>
            """
        return f"""
        <section class="section container reveal">
          {kckr}{hdng}{body}{link_html}
        </section>
        """

    elif section.section_type == "stat_band":
        metrics = c.get("metrics") or []
        cards = "".join(
            f'<div class="proof-card"><div class="proof-card__number">{_s(m.get("number"))}</div>'
            f'<div class="proof-card__label">{_s(m.get("label"))}</div></div>'
            for m in metrics
        )
        return f"""
        <section class="full-bleed dark-band reveal">
          <div class="container"><div class="proof-grid">{cards}</div></div>
        </section>
        """

    elif section.section_type == "card_grid":
        kckr = _kicker(c.get("kicker"))
        hdng = _section_heading(c.get("heading"))
        cards = c.get("cards") or []
        accent_map = {
            "teal": "var(--accent)",
            "purple": "var(--purple)",
            "gold": "var(--gold)",
        }
        cards_html = ""
        for card in cards:
            accent = accent_map.get(card.get("accent", ""), "var(--accent)")
            cta_html = ""
            if card.get("cta_label"):
                ch = _s(card.get("cta_href", "#"))
                cl = _s(card["cta_label"])
                cta_html = f'<a class="inline-link" href="{ch}">{cl}</a>'
            cards_html += f"""
            <div class="visitor-card" style="border-top:3px solid {accent};">
              <h3>{_s(card.get("title"))}</h3>
              <p>{_s(card.get("body"))}</p>
              {cta_html}
            </div>
            """
        return f"""
        <section class="section container reveal">
          {kckr}{hdng}
          <div class="visitor-cards">{cards_html}</div>
        </section>
        """

    elif section.section_type == "interests_bar":
        kckr = _kicker(c.get("kicker"))
        items = c.get("items") or []
        chips = "".join(
            f'<span class="interests-chip">'
            f"{_s(item.get('icon', ''))} "
            f"{_s(item.get('label'))}</span>"
            for item in items
        )
        return f"""
        <section class="section container reveal">
          {kckr}
          <div class="interests-bar">{chips}</div>
        </section>
        """

    elif section.section_type == "project_grid":
        kckr = _kicker(c.get("kicker"))
        hdng = _section_heading(c.get("heading"))
        max_items = c.get("max_items") or 4
        featured_slug = c.get("featured_slug")
        featured_html = ""
        grid_html = ""
        shown = 0
        for proj in projects:
            if shown >= max_items:
                break
            if proj.get("slug") == featured_slug:
                featured_html = _project_card_html(proj, featured=True)
            else:
                grid_html += _project_card_html(proj)
            shown += 1
        if not featured_html and projects:
            featured_html = _project_card_html(projects[0], featured=True)
        return f"""
        <section class="section container reveal">
          {kckr}{hdng}
          {featured_html}
          <div class="project-grid mt-3">{grid_html}</div>
        </section>
        """

    elif section.section_type == "case_study":
        project_slug = c.get("project_slug")
        proj = None
        for p in projects:
            if p.get("slug") == project_slug:
                proj = p
                break
        if not proj:
            return ""
        proj_p = dict(proj.get("payload") or {})
        case = proj_p.get("case_study") or {}
        if not case:
            return ""
        mot = case.get("motivation")
        motivation = f"<p>{_s(mot)}</p>" if mot else ""
        arch_desc = case.get("architecture_description")
        arch = ""
        if arch_desc:
            arch = f'<div class="case-study__architecture">{_s(arch_desc)}</div>'
        decisions_html = "".join(
            _card_html("decision-card", d.get("decision", ""), d.get("context", ""))
            for d in (case.get("key_decisions") or [])
        )
        struggles_html = "".join(
            _card_html("struggle-card", st.get("problem", ""), st.get("resolution", ""))
            for st in (case.get("struggles") or [])
        )
        learnings_html = "".join(
            f'<div class="learning-card"><h4>{_s(lr)}</h4></div>'
            for lr in (case.get("learnings") or [])
        )
        title = _s(proj.get("title"))
        return f"""
        <section class="case-study container">
          {_kicker("Case Study")}
          {_section_heading(title)}
          {motivation}{arch}
          <div class="case-study__decisions">{decisions_html}</div>
          <div class="case-study__struggles">{struggles_html}</div>
          <div class="case-study__learnings">{learnings_html}</div>
        </section>
        """

    elif section.section_type == "photo_row":
        photo_keys = c.get("photo_keys") or []
        tilts = c.get("sticker_tilts") or []
        imgs = ""
        for i, key in enumerate(photo_keys):
            photo = photos.get(key)
            if not photo:
                continue
            tilt = tilts[i] if i < len(tilts) else None
            tilt_cls = f" photo-sticker--tilt-{tilt}" if tilt else ""
            url = _photo_url(photo)
            if url:
                imgs += f"""
                <div class="photo-row__item photo-sticker{tilt_cls}">
                  <img src="{url}" alt="{_s(photo.get("title", ""))}" loading="lazy" />
                </div>
                """
        return (
            f"""
        <section class="section container reveal">
          <div class="photo-row">{imgs}</div>
        </section>
        """
            if imgs
            else ""
        )

    elif section.section_type == "chat_shell":
        heading = c.get("heading") or "Ask Ahmad's brain."
        intro = c.get("intro_text") or (
            "Ask about my work, projects, strengths, interests, or collaboration fit."
        )
        prompts = c.get("starter_prompts") or []
        chips = "".join(
            f'<button class="starter-chip" type="button"'
            f' data-starter-prompt="{_s(p)}">'
            f"{_s(p)}</button>"
            for p in prompts
        )
        turnstile_configured = bool(
            settings.cloudflare_turnstile_site_key and settings.cloudflare_turnstile_secret_key
        )
        turnstile_widget = '<div id="turnstile-widget"></div>' if turnstile_configured else ""
        return f"""
        <section class="section container">
          <div class="public-kicker">Digital Clone</div>
          <h1 class="display-heading display-heading--section">{_s(heading)}</h1>
          <div class="chat-shell mt-2">
            <div class="chat-shell__header">
              <span class="chat-shell__dot"></span>
              <span class="chat-shell__title">open-brain</span>
              <button class="chat-shell__new-btn" data-new-conversation>New conversation</button>
            </div>
            <div class="chat-log" data-public-chat-log>
              <div class="chat-message">
                <strong>Ahmad's Clone</strong>
                <div>{_s(intro)}</div>
              </div>
            </div>
            <div class="starter-prompts">{chips}</div>
            <form class="chat-form" data-public-chat-form>
              <textarea name="question" placeholder="Ask me anything..."></textarea>
              <input type="hidden" name="turnstile_token" value="" />
              {turnstile_widget}
              <button class="cta" type="submit"
                {"disabled" if not turnstile_configured else ""}
              >Ask the clone</button>
              <div class="chat-footnote" data-public-chat-status>
                {
            "Multi-turn conversation. Ask follow-ups."
            if turnstile_configured
            else "Turnstile isn't configured yet, so chat is locked."
        }
              </div>
            </form>
          </div>
        </section>
        """

    elif section.section_type == "custom_html":
        return c.get("html", "")

    return ""


def _render_sections(
    sections: list[WebsiteSection],
    photos: dict,
    projects: list,
) -> str:
    """Render all visible sections to HTML."""
    return "".join(_render_section(s, photos, projects) for s in sections if s.visible)


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
        sections = await list_page_sections(session, "home")
    p = _payload(profile)
    name = _short_name(p)
    photos = p.get("photos") or {}

    if sections:
        content = _render_sections(sections, photos, projects)
    else:
        content = _render_home_fallback(p, name, photos, projects)

    return HTMLResponse(
        render_public_shell(
            page_title=f"{name} — Software Engineer",
            content_html=content,
            active_nav="home",
            page_data={"page": "home"},
            body_class="public-page-home",
            og_description=(
                "Software engineer. Builder of AI systems,"
                " shipped products, and things that remember."
            ),
        )
    )


def _render_home_fallback(p: dict, name: str, photos: dict, projects: list) -> str:
    hero_photo = photos.get("hero")
    personality_photo = photos.get("personality")

    hero_html = f"""
    <section class="hero-home">
      <div class="container">
        <div class="hero-home__text">
          <div class="public-kicker">Software Engineer</div>
          <h1 class="display-heading display-heading--hero">{_s(name)}</h1>
          <p>Software engineer. Builder of AI systems, shipped products,
            and things that remember.</p>
          <div class="hero-home__ctas">
            <a class="cta" href="/work">See the work</a>
            <a class="cta cta--outline" href="/brain">Ask my AI clone</a>
          </div>
        </div>
        <div class="hero-home__photo">
          {_photo_img(hero_photo, loading="eager", alt=f"{name} waterfront portrait")}
        </div>
      </div>
    </section>
    """

    stat_html = """
    <section class="full-bleed dark-band reveal">
      <div class="container">
        <div class="proof-grid">
          <div class="proof-card">
            <div class="proof-card__number">3+</div>
            <div class="proof-card__label">Years at Amazon</div>
          </div>
          <div class="proof-card">
            <div class="proof-card__number">4</div>
            <div class="proof-card__label">Shipped Products</div>
          </div>
          <div class="proof-card">
            <div class="proof-card__number">5</div>
            <div class="proof-card__label">AI Agents in Production</div>
          </div>
          <div class="proof-card">
            <div class="proof-card__number">6+</div>
            <div class="proof-card__label">Years Engineering</div>
          </div>
        </div>
      </div>
    </section>
    """

    about_html = f"""
    <section class="section container reveal">
      <div class="offset-grid offset-grid--60-40">
        <div>
          <div class="public-kicker">What I Build</div>
          <h2 class="display-heading display-heading--section">
            AI systems that solve real problems.</h2>
          <p>I build AI systems that solve real problems. My current work spans
            a personal AI second brain with 5 specialized agents, a conversational
            data analytics platform, and production client sites.
            I care about ownership &mdash; architecture to deployment to operations.</p>
          <a class="inline-link mt-2" href="/about">The full picture</a>
        </div>
        <div class="photo-accent--md">
          {_photo_img(personality_photo, alt="Ahmad with Oscar")}
        </div>
      </div>
    </section>
    """

    first_project = _project_card_html(projects[0], featured=True) if projects else ""
    grid_projects = "".join(_project_card_html(proj) for proj in projects[1:4])
    work_html = f"""
    <section class="section container reveal">
      <div class="public-kicker">Selected Work</div>
      <h2 class="display-heading display-heading--section">
        Built, shipped, running.</h2>
      {first_project}
      <div class="project-grid mt-3">
        {grid_projects}
        <article class="project-card reveal"
          style="display:flex;align-items:center;justify-content:center;">
          <a class="cta" href="/work">View all projects</a>
        </article>
      </div>
    </section>
    """

    chatbot_html = """
    <section class="section container reveal">
      <div class="chatbot-teaser">
        <div class="public-kicker">Digital Clone</div>
        <h2 class="display-heading display-heading--section">
          Talk to my AI clone.</h2>
        <p>Built from real evidence, not a generic chatbot.
          Ask about my work, my projects, whether I'd be a good fit
          for your team.</p>
        <a class="cta" href="/brain">Open the brain</a>
      </div>
    </section>
    """

    contact_items = list(p.get("contact") or p.get("contact_modes") or [])
    contact_links = ""
    for item in contact_items:
        href = item.get("href")
        if not href:
            continue
        contact_links += (
            f'<a href="{_s(href)}" target="_blank" rel="noreferrer">'
            f"{_s(item.get('label') or 'Contact')}</a>"
        )
    location = p.get("location") or "Jersey City, NJ"
    contact_html = f"""
    <section class="section container reveal">
      <div class="contact-strip">
        {contact_links}
        <span>{_s(location)}</span>
      </div>
    </section>
    """

    # Interests bar
    texture = list(p.get("personal_texture") or [])
    interests_data = list(p.get("interests") or [])
    interest_items = texture[:6] if texture else interests_data[:6]
    interests_html = ""
    if interest_items:
        chips = "".join(
            f'<span class="interests-chip">{_s(item)}</span>'
            for item in interest_items
        )
        interests_html = f"""
        <section class="section container reveal">
          {_kicker("Interests")}
          <div class="interests-bar">{chips}</div>
        </section>
        """

    return (
        hero_html + stat_html + about_html + interests_html
        + work_html + chatbot_html + contact_html
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
    mosaic_photos = [item for item in (photos.get("mosaic") or []) if item and item.get("url")]
    roles = list(p.get("roles") or [])
    current_arc = p.get("current_arc") or {}

    hero_html = """
    <section class="hero-inner">
      <div class="container" style="display:block;">
        <div class="public-kicker">About</div>
        <h1 class="display-heading display-heading--hero">The full picture.</h1>
        <p style="max-width:54ch;color:var(--ink-light);font-size:1.08rem;line-height:1.72;">
          From IIT Kharagpur to Amazon to building independently in New York.</p>
      </div>
    </section>
    """

    acts = list(current_arc.get("acts") or [])
    act_cards = ""
    for act in acts[:3]:
        act_cards += f"""
        <div class="act-card">
          <div class="public-kicker">{_s(act.get("period", ""))}</div>
          <h3>{_s(act.get("label", ""))}</h3>
          <p>{_s(act.get("body", ""))}</p>
        </div>
        """
    throughline = current_arc.get("throughline") or ""
    throughline_html = ""
    if throughline:
        throughline_html = f'<blockquote class="throughline-quote">{_s(throughline)}</blockquote>'

    acts_html = f"""
    <section class="section container reveal">
      <div class="public-kicker">The Arc</div>
      <h2 class="display-heading display-heading--section">Three acts, one throughline.</h2>
      <div class="act-cards">{act_cards}</div>
      {throughline_html}
    </section>
    """

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

    # Photo row with sticker tilts
    tilts = ["left", "right", "slight"]
    photo_row_imgs = ""
    for i, item in enumerate(mosaic_photos[1:4]):
        if item and item.get("url"):
            tilt = tilts[i % len(tilts)]
            photo_row_imgs += (
                f'<div class="photo-row__item photo-sticker '
                f'photo-sticker--tilt-{tilt}">'
                f'<img src="{_s(item["url"])}" '
                f'alt="{_s(item.get("title", ""))}" loading="lazy" />'
                f"</div>"
            )
    photo_html = (
        f"""
    <section class="section container reveal">
      <div class="photo-row">{photo_row_imgs}</div>
    </section>
    """
        if photo_row_imgs
        else ""
    )

    # Beyond the Code — use actual data from profile
    texture = list(p.get("personal_texture") or [])
    if texture:
        texture_items = "".join(
            f"<li>{_s(item)}</li>" for item in texture[:8]
        )
    else:
        texture_items = (
            "<li>Five languages: English, Hindi, Telugu, Urdu, Tamil.</li>"
            "<li>Married Annie in 2025. Cat dad to Oscar and Iris.</li>"
            "<li>Cycles, collects Pokemon, loves hip hop and Indian film music.</li>"
            "<li>Japanese markets, tattoos, strong opinions about food.</li>"
        )

    interests_data = list(p.get("interests") or [])
    interest_chips_html = ""
    if interests_data:
        chips = "".join(
            f'<span class="interests-chip">{_s(item)}</span>'
            for item in interests_data[:8]
        )
        interest_chips_html = (
            f'<div class="interests-bar mt-2">{chips}</div>'
        )

    texture_html = f"""
    <section class="section container reveal">
      {_kicker("Beyond the Code")}
      <h2 class="display-heading display-heading--sub">The rest of the picture.</h2>
      <ul class="texture-list">{texture_items}</ul>
      {interest_chips_html}
    </section>
    """

    content = hero_html + acts_html + roles_html + photo_html + texture_html
    return HTMLResponse(
        render_public_shell(
            page_title=f"About {name}",
            content_html=content,
            active_nav="about",
            page_data={"page": "about"},
            body_class="public-page-about",
            og_description="From IIT Kharagpur to Amazon to building independently in New York.",
        )
    )


# ──────────────────────────────────────────────
# Work (new hybrid route) + Projects (compat)
# ──────────────────────────────────────────────


@router.get("/work", response_class=HTMLResponse)
async def public_work() -> HTMLResponse:
    async with async_session() as session:
        profile = await get_public_profile(session)
        projects = await list_public_projects(session)
        sections = await list_page_sections(session, "work")
    p = _payload(profile)
    name = _short_name(p)
    photos = p.get("photos") or {}

    if sections:
        content = _render_sections(sections, photos, projects)
    else:
        content = _render_work_fallback(p, photos, projects)

    return HTMLResponse(
        render_public_shell(
            page_title=f"{name} — Work",
            content_html=content,
            active_nav="projects",
            page_data={"page": "work"},
            body_class="public-page-projects",
            og_description=(
                "Everything here is real. Live URLs, real users, production infrastructure."
            ),
        )
    )


@router.get("/projects", response_class=HTMLResponse)
async def public_projects() -> HTMLResponse:
    """Redirect /projects to /work for backward compat."""
    return RedirectResponse(url="/work", status_code=301)


def _render_work_fallback(p: dict, photos: dict, projects: list) -> str:
    capabilities = list(p.get("capabilities") or [])

    hero_html = f"""
    <section class="hero-inner">
      <div class="container">
        <div>
          <div class="public-kicker">Work</div>
          <h1 class="display-heading display-heading--hero">Work.</h1>
          <p>Everything here is real. Live URLs, real users,
            production infrastructure.</p>
        </div>
        <div class="photo-accent--sm">
          {_photo_img(photos.get("work"), alt="Ahmad street portrait")}
        </div>
      </div>
    </section>
    """

    featured = _project_card_html(projects[0], featured=True) if projects else ""
    grid = "".join(_project_card_html(proj) for proj in projects[1:])
    projects_html = f"""
    <section class="section container">
      {featured}
      <div class="project-grid mt-3">{grid}</div>
    </section>
    """

    cap_tags = "".join(
        f'<span class="capability-tag">{_s(item.get("title"))}</span>' for item in capabilities[:8]
    )
    cap_html = f"""
    <section class="section container reveal">
      <div class="public-kicker">Domains</div>
      <div class="capability-scroll">{cap_tags}</div>
    </section>
    """

    return hero_html + projects_html + cap_html


# ──────────────────────────────────────────────
# Project Detail
# ──────────────────────────────────────────────


@router.get("/work/{slug}", response_class=HTMLResponse)
async def public_project_detail_work(slug: str) -> HTMLResponse:
    return await _render_project_detail(slug)


@router.get("/projects/{slug}", response_class=HTMLResponse)
async def public_project_detail(slug: str) -> HTMLResponse:
    return await _render_project_detail(slug)


async def _render_project_detail(slug: str) -> HTMLResponse:
    async with async_session() as session:
        profile = await get_public_profile(session)
        project = await get_public_project(session, slug)
    if not project:
        raise HTTPException(status_code=404, detail="Public project not found")
    p = _payload(profile)
    photos = p.get("photos") or {}
    proj_p = dict(project.get("payload") or {})
    case_study = proj_p.get("case_study") or {}

    # Hero with photo accent
    project_photo = _photo_img_sticker(photos, "work", "right")
    hero_html = f"""
    <section class="hero-inner">
      <div class="container">
        <div>
          {_kicker("Project")}
          <h1 class="display-heading display-heading--section">
            {_s(project["title"])}</h1>
          <span class="status-badge">
            {_s(proj_p.get("status") or "Active")}</span>
          <p class="mt-2">
            {_s(proj_p.get("tagline") or project.get("summary") or "")}</p>
        </div>
        <div class="photo-accent--sm">{project_photo}</div>
      </div>
    </section>
    """

    # Case study section (primary content from brain evidence)
    case_html = ""
    if case_study:
        mot = case_study.get("motivation")
        motivation = f"<p>{_s(mot)}</p>" if mot else ""
        arch_desc = case_study.get("architecture_description")
        arch = ""
        if arch_desc:
            arch = (
                f'<div class="case-study__architecture">'
                f'{_s(arch_desc)}</div>'
            )
        decisions_html = "".join(
            _card_html(
                "decision-card",
                d.get("decision", ""),
                d.get("context", ""),
            )
            for d in (case_study.get("key_decisions") or [])
        )
        struggles_html = "".join(
            _card_html(
                "struggle-card",
                st.get("problem", ""),
                st.get("resolution", ""),
            )
            for st in (case_study.get("struggles") or [])
        )
        learnings_html = "".join(
            f'<div class="learning-card"><h4>{_s(lr)}</h4></div>'
            for lr in (case_study.get("learnings") or [])
        )
        arch_block = (
            f'{_kicker("Architecture")}{arch}' if arch else ""
        )
        dec_block = (
            f'<div class="case-study__decisions mt-3">'
            f'{_kicker("Key Decisions")}{decisions_html}</div>'
            if decisions_html
            else ""
        )
        str_block = (
            f'<div class="case-study__struggles mt-3">'
            f'{_kicker("Struggles")}{struggles_html}</div>'
            if struggles_html
            else ""
        )
        lrn_block = (
            f'<div class="case-study__learnings mt-3">'
            f'{_kicker("Learnings")}{learnings_html}</div>'
            if learnings_html
            else ""
        )
        case_html = f"""
        <section class="section container">
          {_kicker("Problem &amp; Motivation")}
          {motivation}
          {arch_block}
          {dec_block}
          {str_block}
          {lrn_block}
        </section>
        """

    # Sidebar data
    stack_pills = _pills(list(proj_p.get("stack") or [])[:8])
    links_html = (
        "".join(
            f'<a class="inline-link" href="{_s(item.get("href"))}"'
            f' target="_blank" rel="noreferrer">'
            f"{_s(item.get('label') or 'Open')}</a>"
            for item in list(proj_p.get("links") or [])
            if item.get("href")
        )
        or "<span class='mono-accent'>Links coming soon.</span>"
    )

    # Clean demonstrates — filter garbage
    raw_dem = list(proj_p.get("demonstrates") or [])
    clean_dem = [
        d
        for d in raw_dem
        if d and d.strip() not in {"---", "--", "-", ""}
        and len(d.strip()) >= 10
    ]
    demonstrates_html = _bullet_list(clean_dem[:5])

    # Truncate overview to avoid dump
    raw_summary = proj_p.get("summary") or project.get("summary") or ""
    if len(raw_summary) > 400:
        raw_summary = raw_summary[:397].rstrip() + "..."
    summary_html = _s(raw_summary)

    framing_html = _bullet_list(
        list(proj_p.get("resume_bullets") or [])[:5]
    )

    detail_html = f"""
    <section class="section container">
      <div class="detail-layout">
        <div>
          {_kicker("Overview")}
          <p>{summary_html}</p>
          <div class="mt-3">
            {_kicker("How It Was Framed")}
            {framing_html}
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

    content = hero_html + case_html + detail_html
    return HTMLResponse(
        render_public_shell(
            page_title=_s(project["title"]),
            content_html=content,
            active_nav="projects",
            page_data={"page": "project-detail", "project": project},
            body_class="public-page-project-detail",
            og_description=_s(
                proj_p.get("tagline") or project.get("summary") or ""
            ),
        )
    )


# ──────────────────────────────────────────────
# Brain (new hybrid route) + Open Brain (compat)
# ──────────────────────────────────────────────


@router.get("/brain", response_class=HTMLResponse)
async def public_brain() -> HTMLResponse:
    async with async_session() as session:
        profile = await get_public_profile(session)
        faq = await list_public_faq(session)
        await get_public_answer_policy(session)
        sections = await list_page_sections(session, "brain")
    p = _payload(profile)
    name = _short_name(p)
    photos = p.get("photos") or {}

    if sections:
        projects = []  # brain page doesn't need projects
        content = _render_sections(sections, photos, projects)
    else:
        content = _render_brain_fallback(p, name, faq)

    page_script = ""
    turnstile_configured = bool(
        settings.cloudflare_turnstile_site_key and settings.cloudflare_turnstile_secret_key
    )
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
        content = turnstile_tag + content

    return HTMLResponse(
        render_public_shell(
            page_title=f"Open Brain — {name}",
            content_html=content,
            active_nav="open-brain",
            page_data={"page": "brain", "turnstileConfigured": turnstile_configured},
            page_script=page_script,
            body_class="public-page-open-brain",
            og_description=(
                "A conversational digital clone built from"
                " real evidence. Ask about Ahmad's work,"
                " projects, or fit."
            ),
        )
    )


@router.get("/open-brain", response_class=HTMLResponse)
async def public_open_brain() -> HTMLResponse:
    """Redirect /open-brain to /brain for backward compat."""
    return RedirectResponse(url="/brain", status_code=301)


def _render_brain_fallback(p: dict, name: str, faq: list) -> str:
    thought_garden = list(p.get("thought_garden") or [])
    turnstile_configured = bool(
        settings.cloudflare_turnstile_site_key and settings.cloudflare_turnstile_secret_key
    )

    starter_prompts = [
        "What kind of engineer is Ahmad?",
        "Tell me about duSraBheja",
        "Would he fit an AI infrastructure role?",
    ]
    starter_chips = "".join(
        f'<button class="starter-chip" type="button"'
        f' data-starter-prompt="{_s(prompt)}">{_s(prompt)}</button>'
        for prompt in starter_prompts
    )

    turnstile_widget = ""
    if turnstile_configured:
        turnstile_widget = '<div id="turnstile-widget"></div>'

    chat_html = f"""
    <section class="section container">
      <div class="public-kicker">Digital Clone</div>
      <h1 class="display-heading display-heading--section">Ask Ahmad's brain.</h1>
      <div class="chat-shell mt-2">
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
              &mdash; I'm a conversational version of Ahmad,
              built from real evidence.</div>
          </div>
        </div>
        <div class="starter-prompts">{starter_chips}</div>
        <form class="chat-form" data-public-chat-form>
          <textarea name="question"
            placeholder="Ask me anything..."></textarea>
          <input type="hidden" name="turnstile_token" value="" />
          {turnstile_widget}
          <button class="cta" type="submit"
            {"disabled" if not turnstile_configured else ""}
          >Ask the clone</button>
          <div class="chat-footnote"
            data-public-chat-status>
            {
        "Multi-turn conversation. Ask follow-ups."
        if turnstile_configured
        else "Turnstile isn't configured yet, so chat is locked."
    }
          </div>
        </form>
      </div>
    </section>
    """

    how_html = """
    <section class="section container reveal">
      <div class="public-kicker">How It Works</div>
      <div class="how-cards">
        <div class="how-card">
          <h4>Approved Facts Only</h4>
          <p>Every answer is grounded in a curated allowlist
            of public facts — not the full brain.</p>
        </div>
        <div class="how-card">
          <h4>Multi-Turn</h4>
          <p>Ask follow-ups. The clone tracks conversation
            context across multiple exchanges.</p>
        </div>
        <div class="how-card">
          <h4>Evidence-Led</h4>
          <p>Answers cite real projects, roles, and decisions
            — not hallucinated filler.</p>
        </div>
      </div>
    </section>
    """

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

    garden_tags = "".join(
        f'<span class="thought-tag">{_s(item.get("title") or "")}</span>'
        for item in thought_garden[:8]
    )
    garden_html = (
        f"""
    <section class="section container reveal">
      <div class="public-kicker">Thought Garden</div>
      <div class="thought-garden">{garden_tags}</div>
    </section>
    """
        if garden_tags
        else ""
    )

    return chat_html + how_html + faq_html + garden_html


# ──────────────────────────────────────────────
# Connect (new hybrid route) + Contact (compat)
# ──────────────────────────────────────────────


@router.get("/connect", response_class=HTMLResponse)
async def public_connect() -> HTMLResponse:
    async with async_session() as session:
        profile = await get_public_profile(session)
        sections = await list_page_sections(session, "connect")
    p = _payload(profile)
    name = _short_name(p)
    photos = p.get("photos") or {}

    if sections:
        content = _render_sections(sections, photos, [])
    else:
        content = _render_connect_fallback(p, photos)

    return HTMLResponse(
        render_public_shell(
            page_title=f"Connect — {name}",
            content_html=content,
            active_nav="contact",
            page_data={"page": "connect"},
            body_class="public-page-contact",
            og_description=(
                "Looking for engineering roles where technical depth meets product conviction."
            ),
        )
    )


@router.get("/contact", response_class=HTMLResponse)
async def public_contact() -> HTMLResponse:
    """Redirect /contact to /connect for backward compat."""
    return RedirectResponse(url="/connect", status_code=301)


def _render_connect_fallback(p: dict, photos: dict) -> str:
    contact_items = list(p.get("contact") or p.get("contact_modes") or [])

    hero_html = f"""
    <section class="hero-inner">
      <div class="container">
        <div>
          <div class="public-kicker">Connect</div>
          <h1 class="display-heading display-heading--hero">Let's talk.</h1>
          <p>Looking for engineering roles where technical depth
            meets product conviction. Also take freelance projects.</p>
        </div>
        <div class="photo-accent--sm" style="max-width:300px;">
          {_photo_img(photos.get("contact"), alt="Ahmad with Oscar")}
        </div>
      </div>
    </section>
    """

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
            <a href="{_s(href)}" target="_blank" rel="noreferrer">Open</a>
          </span>
        </div>
        """

    location = p.get("location") or settings.public_profile_location
    contact_html = f"""
    <section class="section container reveal">
      <div class="public-kicker">Channels</div>
      <div class="contact-rows">{rows_html}</div>
      <p class="mt-4" style="font-size:1.25rem;font-weight:600;">{_s(location)}</p>
    </section>
    """

    visitor_html = """
    <section class="section container reveal">
      <div class="visitor-cards">
        <div class="visitor-card">
          <h3>Hiring?</h3>
          <p>I bring 3+ years of Amazon-scale distributed systems,
            AI agent production experience, and end-to-end ownership.
            I ship, deploy, and maintain what I build.</p>
          <a class="inline-link" href="mailto:ahmad.shaik.dev@gmail.com">Email me</a>
        </div>
        <div class="visitor-card">
          <h3>Need a site built?</h3>
          <p>I take on select freelance projects. Full-stack delivery
            from design through deployment &mdash; live client sites
            running in production today.</p>
          <a class="inline-link" href="/work">See past work</a>
        </div>
        <div class="visitor-card">
          <h3>Just curious?</h3>
          <p>Ask my AI clone anything about my work, projects, or fit.
            It's built from real evidence, not a prompt wrapper.</p>
          <a class="inline-link" href="/brain">Talk to the clone</a>
        </div>
      </div>
    </section>
    """

    return hero_html + contact_html + visitor_html


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
