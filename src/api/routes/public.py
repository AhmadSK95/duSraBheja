"""Public-facing site and chatbot routes.

Pages read sections from the DB (WebsiteSection) when available,
falling back to seed-data-driven hardcoded layouts.
"""

from __future__ import annotations

import html
import json
import logging
from pathlib import Path

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


_PHOTO_CAPTIONS: dict[str, str] = {
    "wedding": "November 2025. Courthouse. The LOVE sign was her idea.",
    "pokemon": "The OG starters. Non-negotiable.",
    "cycling": "Oscar waits at the door every time.",
    "personality": "The shirt says \u7e70\u308a\u8fd4\u3059. Repeat.",
    "home": "The workspace. Two monitors, two cats, one rug.",
    "couple": "Jersey City waterfront. Dramatic sky optional.",
    "hero": "Jersey City. North Face. Beanie season.",
}


def _captioned_photo(photos: dict, key: str, *, caption: str = "") -> str:
    """Render a photo with a gradient caption overlay."""
    photo = photos.get(key)
    if not photo:
        return ""
    url = _photo_url(photo)
    if not url:
        return ""
    cap = _s(caption or _PHOTO_CAPTIONS.get(key, ""))
    cap_html = f'<div class="captioned-photo__caption">{cap}</div>' if cap else ""
    return (
        f'<div class="captioned-photo">'
        f'<img src="{url}" alt="{_s(photo.get("title", ""))}" loading="lazy" />'
        f"{cap_html}</div>"
    )


def _photo_break_full(photos: dict, key: str, *, caption: str = "") -> str:
    """Render a full-bleed cinematic photo break."""
    photo = photos.get(key)
    if not photo:
        return ""
    url = _photo_url(photo)
    if not url:
        return ""
    cap_html = ""
    if caption:
        cap_html = (
            f'<div class="photo-break--full__overlay">'
            f'<div class="photo-break--full__caption">{_s(caption)}</div>'
            f"</div>"
        )
    return (
        f'<section class="photo-break--full reveal">'
        f'<img src="{url}" alt="{_s(photo.get("title", ""))}" loading="lazy" />'
        f"{cap_html}</section>"
    )


def _render_currently_feed(p: dict) -> str:
    """Render the 'Currently' living feed section from profile signal data."""
    currently = p.get("currently") or {}
    if not currently:
        return ""
    cards = f"""
    <div class="currently-card">
      <div class="currently-card__label">Watching</div>
      <div class="currently-card__value">{_s(currently.get("watching", ""))}</div>
      <div class="currently-card__detail">{_s(currently.get("watching_detail", ""))}</div>
    </div>
    <div class="currently-card">
      <div class="currently-card__label">Listening</div>
      <div class="currently-card__value">{_s(currently.get("listening", ""))}</div>
      <div class="currently-card__detail">{_s(currently.get("listening_detail", ""))}</div>
    </div>
    <div class="currently-card">
      <div class="currently-card__label">Laughing at</div>
      <div class="currently-card__value">{_s(currently.get("laughing_at", ""))}</div>
      <div class="currently-card__detail">{_s(currently.get("laughing_detail", ""))}</div>
    </div>
    <div class="currently-card">
      <div class="currently-card__label">Stress-watch</div>
      <div class="currently-card__value">{_s(currently.get("stress_watch", ""))}</div>
      <div class="currently-card__detail">{_s(currently.get("stress_detail", ""))}</div>
    </div>
    """
    moments = currently.get("life_moments") or []
    moments_html = ""
    if moments:
        moment_items = "".join(
            f'<div class="currently-moment">'
            f'<span class="currently-moment__date">{_s(m.get("date", ""))}</span>'
            f'<span class="currently-moment__event">{_s(m.get("event", ""))}</span>'
            f"</div>"
            for m in moments[:5]
        )
        moments_html = (
            f'<div class="currently-moments">'
            f'<div class="currently-moments__title">Life Moments</div>'
            f"{moment_items}</div>"
        )
    return f"""
    <section class="section section--warm container reveal">
      <div class="public-kicker public-kicker--gold">Currently</div>
      <div class="currently-feed">{cards}{moments_html}</div>
    </section>
    """


def _load_interests() -> dict:
    """Load interests data from seed JSON file."""
    paths = [
        Path("public-seed/interests.json"),
        Path("/public-seed/interests.json"),
    ]
    for p in paths:
        if p.exists():
            try:
                return json.loads(p.read_text())
            except Exception:
                pass
    return {}


_INTERESTS_COLORS = {
    "youtubers": "var(--accent)",
    "anime": "var(--purple)",
    "shows": "var(--gold)",
    "artists": "var(--accent)",
}


def _render_interests_posters() -> str:
    """Render top-5 interest categories as poster cards."""
    interests = _load_interests()
    categories = [
        ("top5_youtubers", "Top YouTubers", "youtubers"),
        ("top5_anime", "Top Anime", "anime"),
        ("top5_shows", "Top Shows", "shows"),
        ("top5_artists", "Top Artists", "artists"),
    ]
    sections: list[str] = []
    for key, label, cat_cls in categories:
        items = interests.get(key) or []
        accent = _INTERESTS_COLORS.get(cat_cls, "var(--accent)")
        if not items:
            if key == "top5_artists":
                sections.append(
                    f'<div class="interests-category">'
                    f'<div class="interests-category__label"'
                    f' style="color:{accent}">{_s(label)}</div>'
                    f'<div class="poster-placeholder">'
                    f"<span>Spotify data coming soon</span></div></div>"
                )
            continue
        cards = ""
        for i, item in enumerate(items[:5]):
            name = _s(item.get("name", ""))
            subtitle = _s(item.get("subtitle", ""))
            link = _s(item.get("link", "#"))
            cards += (
                f'<a class="poster-card poster-card--{cat_cls}"'
                f' href="{link}" target="_blank" rel="noreferrer">'
                f'<div class="poster-card__rank">{i + 1}</div>'
                f'<div class="poster-card__name">{name}</div>'
                f'<div class="poster-card__sub">{subtitle}</div></a>'
            )
        sections.append(
            f'<div class="interests-category">'
            f'<div class="interests-category__label"'
            f' style="color:{accent}">{_s(label)}</div>'
            f'<div class="poster-row">{cards}</div></div>'
        )
    if not sections:
        return ""
    all_sections = "".join(sections)
    return (
        f'<section class="section container reveal">'
        f'<div class="public-kicker public-kicker--purple">What I\'m Into</div>'
        f'<h2 class="display-heading display-heading--section">'
        f"The non-negotiables.</h2>"
        f'<div class="interests-grid">{all_sections}</div></section>'
    )


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

    elif section.section_type == "photo_break":
        return _photo_break_full(photos, c.get("photo_key", ""), caption=c.get("caption", ""))

    elif section.section_type == "story_block":
        kckr = _kicker(c.get("kicker"))
        hdng = _section_heading(c.get("heading"))
        body = f"<p>{_s(c.get('body'))}</p>" if c.get("body") else ""
        photo_html = _captioned_photo(photos, c.get("photo_key", ""))
        reverse_cls = " story-block--reverse" if c.get("reverse") else ""
        return f"""
        <section class="section container reveal">
          <div class="story-block{reverse_cls}">
            <div class="story-block__photo">{photo_html}</div>
            <div class="story-block__text">{kckr}{hdng}{body}</div>
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
    media_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".mp4": "video/mp4",
        ".webm": "video/webm",
    }
    ext = "".join(path.suffixes[-1:]).lower() if path.suffixes else ""
    media_type = media_types.get(ext)
    return FileResponse(path, media_type=media_type)


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

    hero_html = f"""
    <section class="hero-home">
      <div class="container">
        <div class="hero-home__text">
          <div class="public-kicker">Software Engineer &amp; AI Builder</div>
          <h1 class="display-heading display-heading--hero">{_s(name)}</h1>
          <p>6+ years at Amazon building distributed systems. Now building
            AI-native products &mdash; duSraBheja (personal AI second brain)
            and dataGenie (multi-agent data analytics).
            IIT Kharagpur &amp; NYU Tandon grad.</p>
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
            <div class="proof-card__number">5</div>
            <div class="proof-card__label">AI Agents Built</div>
          </div>
          <div class="proof-card">
            <div class="proof-card__number">4</div>
            <div class="proof-card__label">Live Projects</div>
          </div>
          <div class="proof-card">
            <div class="proof-card__number">5</div>
            <div class="proof-card__label">Languages Spoken</div>
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
          <p>Like duSraBheja &mdash; my personal AI that ingests everything
            I capture across Discord, PDFs, and voice memos, then organizes
            it into searchable knowledge using 5 specialized AI agents.
            I care about ownership &mdash; architecture to deployment to operations.</p>
          <a class="inline-link mt-2" href="/about">The full picture</a>
        </div>
        <div class="photo-accent--sm">
          {_photo_img(photos.get("oscar_selfie"), alt="Ahmad with Oscar at colorful art wall")}
        </div>
      </div>
    </section>
    """

    # Chatbot teaser
    chatbot_html = """
    <section class="section container reveal">
      <div class="chatbot-teaser">
        <div class="public-kicker">Digital Clone</div>
        <h2 class="display-heading display-heading--section">
          I built an AI that knows my work.</h2>
        <p>Not a generic chatbot &mdash; a conversational clone built
          from real evidence. Ask it what I'd build for your problem,
          why I left Amazon, or what anime I'm watching.</p>
        <a class="cta" href="/brain">Open the brain</a>
      </div>
    </section>
    """

    ghibli_accent = ""
    ghibli = photos.get("ghibli_cat")
    if ghibli:
        ghibli_url = _photo_url(ghibli)
        if ghibli_url:
            ghibli_accent = (
                f'<div class="photo-accent--sm" style="max-width:160px;margin:0 auto 1.5rem;">'
                f'<img src="{ghibli_url}" alt="Ghibli illustration" loading="lazy" '
                f'style="width:100%;border-radius:var(--radius-sm);" /></div>'
            )

    first_project = _project_card_html(projects[0], featured=True) if projects else ""
    grid_projects = "".join(_project_card_html(proj) for proj in projects[1:4])
    work_html = f"""
    <section class="section container reveal">
      <div class="public-kicker">Selected Work</div>
      <h2 class="display-heading display-heading--section">
        Built, shipped, running.</h2>
      {ghibli_accent}
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

    # Interests posters
    interests_html = _render_interests_posters()

    # Life photos row
    life_photos_html = ""
    life_photo_keys = ["oscar_stairs", "oscar_couch", "baking", "friends_brooklyn"]
    life_imgs = ""
    for key in life_photo_keys:
        photo = photos.get(key)
        if photo:
            url = _photo_url(photo)
            if url:
                life_imgs += (
                    f'<div class="photo-row__item photo-row__item--sm photo-sticker">'
                    f'<img src="{url}" alt="{_s(photo.get("title", ""))}" loading="lazy" />'
                    f'</div>'
                )
    if life_imgs:
        life_photos_html = f'''
    <section class="section container reveal">
      <div class="photo-row">{life_imgs}</div>
    </section>
    '''

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

    return (
        hero_html
        + stat_html
        + about_html
        + work_html
        + interests_html
        + life_photos_html
        + chatbot_html
        + contact_html
    )


# ──────────────────────────────────────────────
# About
# ──────────────────────────────────────────────


_RESUME_SKILLS = [
    ("Languages", ["Java", "Python", "JavaScript/TypeScript", "SQL"]),
    ("Frontend", ["React", "Next.js", "HTML/CSS", "Vite", "Framer Motion"]),
    (
        "Backend",
        ["FastAPI", "Flask", "Node.js/Express", "Spring",
         "Hibernate", "Apache Camel", "Kafka"],
    ),
    (
        "AI / ML",
        ["Claude API", "OpenAI API", "MCP", "pgvector",
         "sentence-transformers", "DuckDB", "scikit-learn"],
    ),
    (
        "Cloud / Infra",
        ["AWS (production at Amazon)", "DigitalOcean",
         "Docker Compose", "CI/CD", "Nginx", "certbot"],
    ),
    (
        "Data",
        ["PostgreSQL", "pgvector", "DuckDB", "Redis",
         "ARQ", "Elasticsearch", "SQLAlchemy", "Alembic"],
    ),
    (
        "Tools",
        ["Git", "GitHub", "Discord.py", "Ollama",
         "npm", "pip", "Linux/macOS"],
    ),
]


@router.get("/about", response_class=HTMLResponse)
async def public_about() -> HTMLResponse:
    async with async_session() as session:
        profile = await get_public_profile(session)
    p = _payload(profile)
    name = _short_name(p)
    photos = p.get("photos") or {}
    current_arc = p.get("current_arc") or {}

    hero_photo = photos.get("indian_wedding")
    hero_html = f"""
    <section class="about-hero">
      <div class="container">
        <div class="about-hero__text">
          <div class="public-kicker">About</div>
          <h1 class="display-heading display-heading--hero">The full picture.</h1>
          <p>From IIT Kharagpur to Amazon to building
            independently in Jersey City.</p>
        </div>
        <div class="about-hero__photo">
          {_photo_img(hero_photo, loading="eager", alt="Ahmad with Oscar at colorful art wall")}
        </div>
      </div>
    </section>
    """

    # Career arc — 3 acts
    acts = list(current_arc.get("acts") or [])
    act_cards = ""
    for act in acts[:3]:
        act_cards += (
            f'<div class="act-card">'
            f'<div class="public-kicker">{_s(act.get("period", ""))}</div>'
            f'<h3>{_s(act.get("label", ""))}</h3>'
            f'<p>{_s(act.get("body", ""))}</p></div>'
        )
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

    ghibli_accent = ""
    gp = photos.get("ghibli_picnic")
    if gp:
        gpu = _photo_url(gp)
        if gpu:
            ghibli_accent = (
                f'<div class="photo-accent--sm" style="max-width:200px;margin:1.5rem 0;">'
                f'<img src="{gpu}" alt="Ghibli picnic" loading="lazy" '
                f'style="width:100%;border-radius:var(--radius-sm);" /></div>'
            )

    # Experience (full detail)
    experience_html = f'''
<section class="section container reveal">
  <div class="offset-grid offset-grid--65-35">
    <div>
      <div class="public-kicker">Experience</div>
      <h2 class="display-heading display-heading--section">The work history.</h2>
      <div class="roles-table">
        <div class="role-row">
          <span class="role-row__period">Jun 2022 – Sep 2025</span>
          <span class="role-row__org">Amazon</span>
          <span class="role-row__title">Software Development Engineer</span>
          <span class="role-row__summary">New York, NY</span>
        </div>
      </div>
      <ul class="public-bullet-list mt-2">
        <li>Owned frontend and backend services on the advertising
          platform, shipping features for international launches.</li>
        <li>Built Java and Python microservices for ad delivery
          across AWS regions — service meshes, regional failover,
          multi-service debugging.</li>
        <li>Led cross-org coordination for international expansion,
          integrating compliance and localization frameworks.</li>
      </ul>
      <div class="roles-table mt-3">
        <div class="role-row">
          <span class="role-row__period">Oct 2018 – Apr 2020</span>
          <span class="role-row__org">Loylty Rewardz</span>
          <span class="role-row__title">Software Engineer</span>
          <span class="role-row__summary">Mumbai, India</span>
        </div>
      </div>
      <ul class="public-bullet-list mt-2">
        <li>Core team on loyalty rewards engine processing millions
          of daily transactions. Java/Hibernate/SQL — learned ORMs
          break on complex batch ops.</li>
        <li>Designed Apache Camel file ingestion pipelines
          normalizing dozens of formats into a single data model.</li>
        <li>Led migration from batch processing to Apache Kafka
          for real-time event streaming.</li>
      </ul>
      <div class="roles-table mt-3">
        <div class="role-row">
          <span class="role-row__period">Jul 2017 – Oct 2018</span>
          <span class="role-row__org">Loylty Rewardz</span>
          <span class="role-row__title">Management Trainee</span>
          <span class="role-row__summary">Mumbai, India</span>
        </div>
      </div>
      <ul class="public-bullet-list mt-2">
        <li>Shipped production code within first quarter. Built
          ETL jobs in Talend for data migration and real-time
          transaction sync.</li>
      </ul>
    </div>
    <div class="photo-accent--sm">
      {_photo_img(photos.get("work"), alt="Ahmad NYC street portrait")}
    </div>
  </div>
</section>
'''

    # Education
    education_html = f'''
<section class="section container reveal">
  <div class="offset-grid offset-grid--65-35">
    <div>
      <div class="public-kicker">Education</div>
      <h2 class="display-heading display-heading--section">IIT Kharagpur + NYU Tandon.</h2>
      <div class="education-rows">
        <div class="education-row">
          <span class="education-row__period">2021 – 2022</span>
          <div>
            <div class="education-row__school">NYU Tandon School of Engineering</div>
            <div class="education-row__degree">Master of Science, Electrical Engineering</div>
            <p class="mt-1" style="color:var(--ink-light);font-size:0.92rem;">
              Coursework: Machine Learning, Deep Learning, Big Data.
              Project: Automated Tagging of News Articles (LDA, search engine UI).</p>
          </div>
        </div>
        <div class="education-row">
          <span class="education-row__period">2013 – 2017</span>
          <div>
            <div class="education-row__school">Indian Institute of Technology, Kharagpur</div>
            <div class="education-row__degree">Bachelor of Technology, Electrical Engineering</div>
            <p class="mt-1" style="color:var(--ink-light);font-size:0.92rem;">
              Projects: Automated Background Modelling (Computer Vision, optical flow),
              Smart Home Energy Management (IoT). KVPY Scholar. NSEP Qualifier.</p>
          </div>
        </div>
      </div>
    </div>
    <div class="photo-accent--sm">
      {_photo_img(photos.get("indian_wedding"), alt="Ahmad and Annie in Indian attire")}
    </div>
  </div>
</section>
'''

    # Skills grid
    skill_sections = ""
    for label, items in _RESUME_SKILLS:
        pills = "".join(f'<span class="pill">{_s(item)}</span>' for item in items)
        skill_sections += (
            f'<div class="skill-category">'
            f'<div class="skill-category__label">{_s(label)}</div>'
            f'<div class="pill-list">{pills}</div></div>'
        )
    skills_html = f'''
<section class="section container reveal">
  <div class="public-kicker">Technical Stack</div>
  <h2 class="display-heading display-heading--section">What I work with.</h2>
  <div class="skills-grid">{skill_sections}</div>
</section>
'''

    # Life facts with photo grid
    life_photo_keys = [
        "oscar_stairs", "oscar_window", "oscar_chair",
        "oscar_office", "oscar_couch", "baking",
    ]
    life_photo_imgs = ""
    for key in life_photo_keys:
        ph = photos.get(key)
        if ph:
            pu = _photo_url(ph)
            if pu:
                life_photo_imgs += (
                    f'<div class="photo-gallery__item">'
                    f'<img src="{pu}" alt="{_s(ph.get("title", ""))}" loading="lazy" /></div>'
                )

    life_html = f'''
<section class="section container reveal">
  <div class="public-kicker public-kicker--gold">Life</div>
  <h2 class="display-heading display-heading--section">The human side.</h2>
  <div class="offset-grid offset-grid--60-40">
    <div>
      <div class="life-facts">
        <div class="life-fact">
          <strong>Home</strong>
          <span>Jersey City, NJ</span></div>
        <div class="life-fact">
          <strong>Married</strong>
          <span>Annie Harpel, November 2025</span></div>
        <div class="life-fact">
          <strong>Cats</strong>
          <span>Oscar (orange tabby, 7yo) &amp; Iris</span></div>
        <div class="life-fact">
          <strong>Languages</strong>
          <span>English, Hindi, Telugu, Urdu, Tamil</span></div>
        <div class="life-fact">
          <strong>After hours</strong>
          <span>Anime, Indian standup, hip hop, cycling,
            Pokemon collecting</span></div>
      </div>
    </div>
    <div class="photo-gallery">{life_photo_imgs}</div>
  </div>
</section>
'''

    # Photo gallery from all gallery photos
    gallery_photos = list(photos.get("gallery") or [])
    gallery_imgs = ""
    for ph in gallery_photos:
        url = _photo_url(ph)
        if url:
            gallery_imgs += (
                f'<div class="photo-gallery__item">'
                f'<img src="{url}" alt="{_s(ph.get("title", ""))}" loading="lazy" /></div>'
            )
    gallery_html = ""
    if gallery_imgs:
        gallery_html = f'''
    <section class="section container reveal">
      <div class="public-kicker public-kicker--purple">Life in Pictures</div>
      <div class="photo-gallery">{gallery_imgs}</div>
    </section>
    '''

    # Interests posters — top 5s with links
    interests_html = _render_interests_posters()

    content = (
        hero_html + acts_html + ghibli_accent
        + experience_html + education_html + skills_html
        + life_html + gallery_html + interests_html
    )
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
        <div class="photo-accent--lg">
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
        project = await get_public_project(session, slug)
    if not project:
        raise HTTPException(status_code=404, detail="Public project not found")
    proj_p = dict(project.get("payload") or {})
    case_study = proj_p.get("case_study") or {}
    repo_history = proj_p.get("repo_history") or {}

    # ── Hero (clean, no photo) ──
    hero_html = f"""
    <section class="hero-inner">
      <div class="container">
        <div>
          {_kicker("Case Study")}
          <h1 class="display-heading display-heading--section">
            {_s(project["title"])}</h1>
          <span class="status-badge">
            {_s(proj_p.get("status") or "Active")}</span>
          <p class="mt-2">
            {_s(proj_p.get("tagline") or project.get("summary") or "")}</p>
        </div>
        <div class="detail-sidebar">
          <div class="detail-sidebar__block">
            <h4>Stack</h4>
            {_pills(list(proj_p.get("stack") or [])[:8])}
          </div>
          <div class="detail-sidebar__block">
            <h4>Links</h4>
            <div class="link-column">{
    "".join(
        f'<a class="inline-link" href="{_s(item.get("href"))}"'
        f' target="_blank" rel="noreferrer">'
        f"{_s(item.get('label') or 'Open')}</a>"
        for item in list(proj_p.get("links") or [])
        if item.get("href")
    ) or "<span class='mono-accent'>Links coming soon.</span>"
    }</div>
          </div>
        </div>
      </div>
    </section>
    """

    # ── Problem statement ──
    motivation = case_study.get("motivation") or repo_history.get("executive_summary") or ""
    problem_html = ""
    if motivation:
        problem_html = f"""
        <section class="section container reveal">
          {_kicker("The Problem")}
          <div class="cs-problem"><p>{_s(motivation)}</p></div>
        </section>
        """

    # ── Architecture diagram ──
    arch_html = ""
    diagrams = repo_history.get("architecture_diagrams") or []
    arch_desc = case_study.get("architecture_description") or ""
    if diagrams:
        diagram_blocks = ""
        for d in diagrams[:2]:
            title = d.get("title", "")
            diag_text = d.get("diagram", "")
            explanation = d.get("explanation", "")
            diag_html = ""
            if diag_text:
                diag_html = (
                    f'<div class="cs-arch__terminal">'
                    f'<div class="cs-arch__terminal-bar">'
                    f'<span class="cs-arch__terminal-dot"></span>'
                    f'<span class="cs-arch__terminal-title">'
                    f'{_s(title) if title else "architecture"}</span></div>'
                    f'<pre class="cs-arch__diagram">{_s(diag_text)}</pre></div>'
                )
            # Truncate explanation
            if explanation and len(explanation) > 300:
                explanation = explanation[:297].rstrip() + "..."
            expl_html = (
                f'<div class="cs-arch__explanation">{_s(explanation)}</div>'
                if explanation else ""
            )
            diagram_blocks += f"{diag_html}{expl_html}"
        arch_html = f"""
        <section class="section container reveal">
          {_kicker("Architecture")}
          <div class="cs-arch">{diagram_blocks}</div>
        </section>
        """
    elif arch_desc:
        arch_html = f"""
        <section class="section container reveal">
          {_kicker("Architecture")}
          <div class="cs-arch">
            <div class="cs-arch__block">
              <div class="cs-arch__diagram">{_s(arch_desc)}</div>
            </div>
          </div>
        </section>
        """

    # ── Decision history slider ──
    all_decisions = list(case_study.get("key_decisions") or [])
    arch_decisions = list(repo_history.get("architectural_decisions") or [])
    for ad in arch_decisions:
        all_decisions.append(
            {"decision": ad.get("title", ""), "context": ad.get("rationale", "")}
        )
    decisions_html = ""
    if all_decisions:
        slides = ""
        for i, d in enumerate(all_decisions):
            title = _s(d.get("decision") or d.get("title", ""))
            context = _s(d.get("context") or d.get("rationale", ""))
            slides += (
                f'<div class="cs-decision-slide" data-slide="{i}">'
                f'<div class="cs-decision-slide__num">#{i + 1}</div>'
                f'<h4>{title}</h4>'
                f"<p>{context}</p></div>"
            )
        decisions_html = f"""
        <section class="section container reveal">
          {_kicker("Key Decisions")}
          <div class="cs-decision-slider" data-decision-slider>
            <div class="cs-decision-slider__track">{slides}</div>
            <div class="cs-decision-slider__controls">
              <button class="cs-slider-btn" data-slider-prev
                aria-label="Previous">&larr;</button>
              <span class="cs-slider-counter" data-slider-counter>
                1 / {len(all_decisions)}</span>
              <button class="cs-slider-btn" data-slider-next
                aria-label="Next">&rarr;</button>
            </div>
          </div>
        </section>
        """

    # ── Challenges ──
    all_challenges = list(case_study.get("struggles") or [])
    repo_challenges = list(repo_history.get("challenges") or [])
    for rc in repo_challenges:
        all_challenges.append(
            {"problem": rc.get("title", ""), "resolution": rc.get("solution", "")}
        )
    challenges_html = ""
    if all_challenges:
        cards = ""
        for ch in all_challenges:
            prob = _s(ch.get("problem") or ch.get("title", ""))
            resolution = ch.get("resolution") or ch.get("solution") or ""
            res_html = (
                f'<div class="cs-challenge__resolution">{_s(resolution)}</div>'
                if resolution
                else ""
            )
            cards += (
                f'<div class="cs-challenge">'
                f"<h4>{prob}</h4>{res_html}</div>"
            )
        challenges_html = f"""
        <section class="section container reveal">
          {_kicker("Challenges")}
          <div class="cs-challenges">{cards}</div>
        </section>
        """

    # ── Learnings ──
    raw_learnings = list(case_study.get("learnings") or [])
    learnings_html = ""
    if raw_learnings:
        items = "".join(
            f'<div class="cs-learning"><span>{_s(lr)}</span></div>'
            for lr in raw_learnings
        )
        learnings_html = f"""
        <section class="section container reveal">
          {_kicker("Learnings")}
          <div class="cs-learnings">{items}</div>
        </section>
        """

    # ── Demo video ──
    demo_video_map = {
        "balkan-barbershop-website": "balkan_barbers_demo.mp4",
        "kaffa-espresso-bar-website": "kaffa_espresso_bar_demo.mp4",
        "datagenie": "datagenie_demo.mp4",
    }
    demo_file = demo_video_map.get(slug)
    media_html = ""
    if demo_file:
        preload = "none" if "kaffa" in demo_file else "metadata"
        media_html = (
            f'<section class="section container reveal">'
            f'{_kicker("Demo")}'
            f'<video class="cs-demo-video" controls preload="{preload}">'
            f'<source src="/public-assets/profile/{_s(demo_file)}" type="video/mp4">'
            f'Your browser does not support video.</video></section>'
        )

    content = (
        hero_html + problem_html + arch_html
        + decisions_html + challenges_html + learnings_html + media_html
    )
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
        "Why did he leave Amazon?",
        "Tell me about duSraBheja",
        "What anime is he watching?",
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
        <div class="photo-accent--lg">
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
        <div class="visitor-card" style="border-top:3px solid var(--accent);">
          <h3>Hiring?</h3>
          <p>If your product needs AI that actually works in production
            &mdash; not just a demo &mdash; I'm your person.
            Distributed systems at Amazon scale, 5 AI agents in production,
            and I own everything I ship end to end.</p>
          <a class="inline-link" href="mailto:ahmad2609.as@gmail.com">Email me</a>
        </div>
        <div class="visitor-card" style="border-top:3px solid var(--purple);">
          <h3>Need a site built?</h3>
          <p>I build for real businesses &mdash; a barbershop with Stripe
            payments and admin dashboards, a coffee shop with full
            infrastructure. Not templates. Real products.</p>
          <a class="inline-link" href="/work">See the work</a>
        </div>
        <div class="visitor-card" style="border-top:3px solid var(--gold);">
          <h3>Just curious?</h3>
          <p>Ask my AI clone anything. It knows my projects, my career,
            my stack opinions, and my cats' names. Built from real
            evidence, not a prompt wrapper.</p>
          <a class="inline-link" href="/brain">Talk to the clone</a>
        </div>
      </div>
    </section>
    """

    # Photo row before visitor cards
    photo_row_html = f"""
    <section class="section container reveal">
      <div class="captioned-photo-pair">
        {_captioned_photo(photos, "couple")}
        {_captioned_photo(photos, "photo_break", caption="Jersey City skyline. Golden hour.")}
      </div>
    </section>
    """

    return hero_html + contact_html + photo_row_html + visitor_html


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
