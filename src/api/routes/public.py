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
    get_public_surface_ops_status,
    list_public_faq,
    list_public_projects,
    public_chat_captcha_enabled,
    public_chat_enabled,
)

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


def _project_payload(project: dict) -> dict:
    return dict(project.get("payload") or project)


def _project_collections(projects: list[dict]) -> tuple[list[dict], list[dict]]:
    flagship = [project for project in projects if _project_payload(project).get("tier") != "secondary"]
    secondary = [project for project in projects if _project_payload(project).get("tier") == "secondary"]
    return flagship, secondary


def _render_update_window(window: dict | None, *, compact: bool = False) -> str:
    payload = dict(window or {})
    items = list(payload.get("items") or [])
    if not items:
        return ""
    cls = " update-window--compact" if compact else ""
    cards = "".join(
        f'<article class="update-card">'
        f'<div class="update-card__meta">{_s(item.get("project_title") or item.get("timestamp_label") or "")}</div>'
        f'<h3>{_s(item.get("headline") or "Update")}</h3>'
        f'<p>{_s(item.get("summary") or "")}</p>'
        f'<div class="update-card__stamp">{_s(item.get("timestamp_label") or "")}</div>'
        f"</article>"
        for item in items[:4]
    )
    return (
        f'<section class="update-window{cls}">'
        f'<div class="public-kicker">Latest Work</div>'
        f'<h2 class="display-heading display-heading--sub">{_s(payload.get("title") or "Latest brain updates")}</h2>'
        f'<div class="update-window__rail">{cards}</div>'
        f"</section>"
    )


def _render_taste_modules(p: dict) -> str:
    modules = list(p.get("taste_modules") or [])
    if not modules:
        return ""
    rows = []
    for module in modules[:4]:
        cards = "".join(
            f'<a class="taste-card" href="{_s(item.get("link") or "#")}"'
            f' {"target=\"_blank\" rel=\"noreferrer\"" if item.get("link") else ""}>'
            f'<div class="taste-card__rank">{_s(item.get("rank"))}</div>'
            f'<div class="taste-card__body"><strong>{_s(item.get("name"))}</strong>'
            f'<span>{_s(item.get("subtitle") or "")}</span></div></a>'
            for item in list(module.get("items") or [])[:5]
        )
        rows.append(
            f'<section class="taste-module">'
            f'<div class="taste-module__meta">{_s(module.get("eyebrow") or "")}</div>'
            f'<h3>{_s(module.get("title") or "")}</h3>'
            f'<div class="taste-module__rail">{cards}</div>'
            f"</section>"
        )
    return (
        f'<section class="section container reveal">'
        f'<div class="public-kicker public-kicker--purple">Taste Signals</div>'
        f'<h2 class="display-heading display-heading--section">The ranked obsessions are part of the interface.</h2>'
        f'<div class="taste-module-stack">{"".join(rows)}</div>'
        f"</section>"
    )


def _render_architecture_diagram(spec: dict | None) -> str:
    diagram = dict(spec or {})
    lanes = list(diagram.get("lanes") or [])
    if not lanes:
        return ""
    lane_html = "".join(
        f'<section class="architecture-lane">'
        f'<div class="architecture-lane__label">{_s(lane.get("label") or "")}</div>'
        f'<div class="architecture-lane__nodes">'
        f'{"".join(f"<article class=\"architecture-node\"><h4>{_s(node.get('label') or '')}</h4><p>{_s(node.get('detail') or '')}</p></article>" for node in list(lane.get("nodes") or []))}'
        f"</div></section>"
        for lane in lanes
    )
    edge_html = "".join(
        f'<div class="architecture-edge"><span>{_s(edge.get("from") or "")}</span>'
        f'<span>{_s(edge.get("label") or "")}</span>'
        f'<span>{_s(edge.get("to") or "")}</span></div>'
        for edge in list(diagram.get("edges") or [])[:8]
    )
    callouts = "".join(
        f'<div class="architecture-callout"><strong>{_s(item.get("label") or "")}</strong><p>{_s(item.get("body") or "")}</p></div>'
        for item in list(diagram.get("callouts") or [])[:3]
    )
    return (
        f'<section class="architecture-diagram">'
        f'<div class="architecture-diagram__header"><h3>{_s(diagram.get("title") or "Architecture")}</h3>'
        f'<p>{_s(diagram.get("caption") or "")}</p></div>'
        f'<div class="architecture-diagram__lanes">{lane_html}</div>'
        f'<div class="architecture-diagram__edges">{edge_html}</div>'
        f'<div class="architecture-diagram__callouts">{callouts}</div>'
        f"</section>"
    )


def _render_decision_slider(decisions: list[dict[str, str]]) -> str:
    if not decisions:
        return ""
    slides = "".join(
        f'<article class="cs-decision-slide">'
        f'<div class="public-kicker">{_s(item.get("title") or "Decision")}</div>'
        f'<h3>{_s(item.get("decision") or item.get("title") or "")}</h3>'
        f'<p>{_s(item.get("rationale") or "")}</p>'
        f'<div class="cs-decision-slide__tradeoff">{_s(item.get("tradeoff") or "")}</div>'
        f"</article>"
        for item in decisions[:6]
    )
    return (
        f'<div class="decision-slider" data-decision-slider>'
        f'<div class="decision-slider__nav"><button type="button" data-slider-prev>Prev</button>'
        f'<span data-slider-counter>1 / {len(decisions[:6])}</span>'
        f'<button type="button" data-slider-next>Next</button></div>'
        f'<div class="decision-slider__viewport">{slides}</div>'
        f"</div>"
    )


def _render_flagship_showcase(project: dict, *, rank: int) -> str:
    payload = _project_payload(project)
    case_study = dict(payload.get("curated_case_study") or {})
    update_window = dict(payload.get("daily_update_window") or {})
    proof = "".join(f"<li>{_s(item)}</li>" for item in list(payload.get("proof") or [])[:3])
    return (
        f'<article class="flagship-showcase flagship-showcase--{rank} reveal">'
        f'<div class="flagship-showcase__rank">0{rank + 1}</div>'
        f'<div class="flagship-showcase__body">'
        f'<div class="public-kicker">{_s(case_study.get("hero_label") or payload.get("status") or "Case Study")}</div>'
        f'<h3>{_s(project.get("title") or payload.get("title"))}</h3>'
        f'<p>{_s(payload.get("summary") or payload.get("tagline") or "")}</p>'
        f'{_pills(list(payload.get("stack") or [])[:6])}'
        f'{"<ul class=\"project-proof-list\">" + proof + "</ul>" if proof else ""}'
        f'<div class="link-row mt-2"><a class="inline-link" href="/projects/{_s(project.get("slug"))}">Read case study</a>'
        f'{"<a class=\"inline-link\" href=\"/projects/" + _s(project.get("slug")) + "#demo\">Watch demo</a>" if payload.get("demo_asset") else ""}</div>'
        f"{_render_update_window({'title': 'Latest work on this project', 'items': list(update_window.get('items') or [])[:1]}, compact=True)}"
        f"</div></article>"
    )


def _demo_video_html(project: dict, *, compact: bool = False) -> str:
    payload = _project_payload(project)
    demo_asset = payload.get("demo_asset")
    if not demo_asset:
        return ""
    cls = " demo-video-card--compact" if compact else ""
    title = _s(project.get("title") or payload.get("title"))
    return (
        f'<article class="demo-video-card{cls}">'
        f'<div class="public-kicker">Live demo</div>'
        f"<h3>{title}</h3>"
        f'<video controls preload="metadata">'
        f'<source src="/public-assets/profile/{_s(demo_asset)}" type="video/mp4">'
        f"Your browser does not support video.</video>"
        f"</article>"
    )


def _photo_tile(photo: dict | None, *, cls: str = "") -> str:
    if not photo:
        return ""
    url = _photo_url(photo)
    if not url:
        return ""
    extra_cls = f" {cls}" if cls else ""
    return (
        f'<div class="editorial-photo{extra_cls}">'
        f'<img src="{url}" alt="{_s(photo.get("title", ""))}" loading="lazy" />'
        f"</div>"
    )


def _project_card_html(project: dict, *, featured: bool = False) -> str:
    p = _project_payload(project)
    stack = p.get("stack") or []
    slug = project.get("slug") or ""
    featured_cls = " project-card--featured" if featured else ""
    tier = str(p.get("tier") or "flagship")
    proof = list(p.get("proof") or [])[:3]
    proof_html = "".join(f"<li>{_s(item)}</li>" for item in proof if item)
    link_items = [
        f'<a class="inline-link" href="{_s(item.get("href"))}" target="_blank" rel="noreferrer">'
        f"{_s(item.get('label') or 'Open')}</a>"
        for item in (p.get("links") or [])
        if item.get("href")
    ]
    if tier == "flagship":
        link_items.insert(0, f'<a class="inline-link" href="/projects/{_s(slug)}">Read case study</a>')
    elif p.get("links"):
        link_items.insert(0, '<span class="mono-accent">Secondary proof</span>')
    demo_link = ""
    if p.get("demo_asset"):
        demo_link = (
            f'<a class="inline-link" href="/projects/{_s(slug)}#demo">'
            f"{_s('Watch demo' if tier == 'flagship' else 'View demo')}</a>"
        )
        link_items.append(demo_link)
    return f"""
    <article class="project-card{featured_cls} reveal">
      <div class="public-kicker">{_s(p.get("status") or ("Case Study" if tier == "flagship" else "Project Proof"))}</div>
      <h3>{_s(project.get("title"))}</h3>
      <p>{_s(p.get("tagline") or project.get("summary") or "")}</p>
      {_pills(stack[:6])}
      {'<ul class="project-proof-list">' + proof_html + '</ul>' if proof_html else ''}
      <div class="link-row mt-2">
        {"".join(link_items)}
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
    p = _payload(profile)
    name = _short_name(p)
    photos = p.get("photos") or {}
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
    flagship_projects, _secondary_projects = _project_collections(projects)
    hero_photo = photos.get("hero")
    professional_summary = p.get("professional_summary") or p.get("hero_summary") or ""
    identity_stack = list(p.get("identity_stack") or [])[:3]
    freshness = dict(p.get("freshness") or {})
    proof_cards = [
        ("6+", "Years building software"),
        ("4", "Flagship case studies"),
        ("2", "Client demos live"),
        ("1", "Brain refreshing the surface daily"),
    ]
    proof_html = "".join(
        f'<div class="proof-card"><div class="proof-card__number">{_s(number)}</div>'
        f'<div class="proof-card__label">{_s(label)}</div></div>'
        for number, label in proof_cards
    )
    identity_html = "".join(
        f'<li>{_s(item)}</li>' for item in identity_stack if item and item != professional_summary
    )
    freshness_html = (
        f'<div class="freshness-pill">Last brain refresh: {_s(freshness.get("last_refreshed_at") or "Now")}</div>'
    )
    hero_html = f"""
    <section class="hero-home hero-home--sleek">
      <div class="container hero-home__grid">
        <div class="hero-home__text">
          <div class="public-kicker">Software Engineer · Builder · Systems thinker</div>
          <h1 class="display-heading display-heading--hero">{_s(name)}</h1>
          <p>{_s(professional_summary)}</p>
          {'<ul class="hero-note-list">' + identity_html + '</ul>' if identity_html else ''}
          <div class="hero-home__ctas">
            <a class="cta" href="/work">See the work</a>
            <a class="cta cta--outline" href="/about">Read the story</a>
          </div>
          {freshness_html}
        </div>
        <div class="hero-home__photo hero-home__photo--compact">
          {_photo_img(hero_photo, loading="eager", alt=f"{name} portrait")}
        </div>
      </div>
    </section>
    <section class="section section--tight container reveal">
      <div class="proof-grid">{proof_html}</div>
    </section>
    """

    open_brain_topics = list(p.get("open_brain_topics") or [])[:4]
    topic_cards = "".join(
        f'<div class="brain-topic-card"><h3>{_s(item.get("title", ""))}</h3>'
        f'<p>{_s(item.get("summary", ""))}</p></div>'
        for item in open_brain_topics
    )
    chat_line = (
        "Captcha is active for abuse control."
        if public_chat_captcha_enabled()
        else "Chat is live without captcha because the public clone is running in no-captcha mode."
    )
    open_brain_html = f"""
    <section class="section container reveal">
      <div class="open-brain-hero open-brain-hero--premium">
        <div>
          <div class="public-kicker">Open Brain</div>
          <h2 class="display-heading display-heading--section">The site can explain me in my own language.</h2>
          <p>This is the public-safe layer of the same brain I use to track work, projects, signals, and operating context. It knows the approved version of the story and updates with the brain refresh cycle.</p>
          <div class="link-row mt-2">
            <a class="cta" href="/brain">Open the brain</a>
            <a class="inline-link" href="/brain">{_s(chat_line)}</a>
          </div>
        </div>
        <div class="brain-topic-grid">{topic_cards}</div>
      </div>
    </section>
    """

    latest_updates_html = _render_update_window(p.get("daily_update_window"))

    summary_photo = _photo_tile(photos.get("oscar_selfie"), cls="editorial-photo--small")
    builder_summary_html = f"""
    <section class="section container reveal">
      <div class="offset-grid offset-grid--60-40">
        <div>
          <div class="public-kicker">Builder summary</div>
          <h2 class="display-heading display-heading--section">A concise version of the resume, but with actual signal.</h2>
          <p>{_s(p.get("latest_work_summary") or "The strongest proof of what I want to do next lives in the products and client work I have actually shipped.")}</p>
          <a class="inline-link mt-2" href="/about">See the full resume story on the About page</a>
        </div>
        <div>{summary_photo}</div>
      </div>
    </section>
    """

    showcase_html = "".join(
        _render_flagship_showcase(project, rank=index)
        for index, project in enumerate(flagship_projects[:4])
    )
    work_html = f"""
    <section class="section container reveal">
      <div class="public-kicker">Flagship Work</div>
      <h2 class="display-heading display-heading--section">Four projects I can defend in detail.</h2>
      <div class="flagship-showcase-grid">{showcase_html}</div>
      <div class="link-row mt-3">
        <a class="cta cta--outline" href="/work">See all work</a>
      </div>
    </section>
    """

    demo_projects = [
        project
        for project in flagship_projects
        if project.get("slug") in {"balkan-barbershop-website", "kaffa-espresso-bar-website"}
    ]
    demo_html = "".join(_demo_video_html(project, compact=True) for project in demo_projects[:2])
    demos_section = (
        f"""
        <section class="section container reveal" id="demo-proof">
          <div class="public-kicker">Client Demo Proof</div>
          <h2 class="display-heading display-heading--section">Balkan and Kaffa are real client surfaces, not fake portfolio screens.</h2>
          <div class="demo-video-grid">{demo_html}</div>
        </section>
        """
        if demo_html
        else ""
    )

    currently_html = _render_currently_feed(p)
    taste_html = _render_taste_modules(p)

    contact_items = list(p.get("contact") or p.get("contact_modes") or [])
    contact_links = "".join(
        f'<a href="{_s(item.get("href"))}" target="_blank" rel="noreferrer">{_s(item.get("label") or "Contact")}</a>'
        for item in contact_items
        if item.get("href")
    )
    contact_html = f"""
    <section class="section container reveal">
      <div class="closing-cta">
        <div>
          <div class="public-kicker">Connect</div>
          <h2 class="display-heading display-heading--section">If the work feels aligned, reach out.</h2>
          <p>I am looking for high-ownership engineering work where systems depth, product taste, and AI fluency all matter at once.</p>
        </div>
        <div class="closing-cta__links">{contact_links}</div>
      </div>
    </section>
    """

    return (
        hero_html
        + open_brain_html
        + latest_updates_html
        + builder_summary_html
        + work_html
        + demos_section
        + currently_html
        + taste_html
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
    roles = list(p.get("roles") or [])
    education = list(p.get("education") or [])
    skills = list(p.get("skills") or [])
    personal_signals = dict(p.get("personal_signals") or {})
    resume_sections = list(p.get("resume_sections") or [])
    hero_html = f"""
    <section class="about-hero">
      <div class="container">
        <div class="about-hero__text">
          <div class="public-kicker">About</div>
          <h1 class="display-heading display-heading--hero">The resume, with the actual person still intact.</h1>
          <p>I moved from IIT Kharagpur to New York, spent years in enterprise and Amazon-scale systems, then stepped into a builder phase where I care more about ownership, product conviction, and work that feels worth carrying.</p>
        </div>
        <div class="about-hero__photo about-hero__photo--compact">
          {_photo_img(photos.get("indian_wedding"), loading="eager", alt=f"{name} in wedding attire")}
        </div>
      </div>
    </section>
    """

    acts = list(current_arc.get("acts") or [])
    act_cards = "".join(
        f'<div class="act-card"><div class="public-kicker">{_s(act.get("period", ""))}</div>'
        f'<h3>{_s(act.get("label", ""))}</h3><p>{_s(act.get("body", ""))}</p></div>'
        for act in acts[:3]
    )
    throughline = current_arc.get("throughline") or ""
    throughline_html = (
        f'<blockquote class="throughline-quote">{_s(throughline)}</blockquote>' if throughline else ""
    )
    arc_html = f"""
    <section class="section container reveal">
      <div class="public-kicker">Three-act arc</div>
      <h2 class="display-heading display-heading--section">India, New York, then the builder phase.</h2>
      <div class="act-cards">{act_cards}</div>
      {throughline_html}
    </section>
    """

    resume_cards = "".join(
        f'<div class="resume-section-card"><h3>{_s(section.get("title", ""))}</h3>'
        f'<p>{_s(section.get("summary", ""))}</p></div>'
        for section in resume_sections[:4]
    )
    resume_overview_html = f"""
    <section class="section container reveal">
      <div class="public-kicker">Resume structure</div>
      <div class="resume-section-grid">{resume_cards}</div>
    </section>
    """

    experience_rows = ""
    for role in roles:
        bullets_html = "".join(f"<li>{_s(item)}</li>" for item in list(role.get("bullets") or [])[:3])
        details_html = (
            f'<ul class="public-bullet-list">{bullets_html}</ul>'
            if bullets_html
            else f'<p>{_s(role.get("summary", ""))}</p>'
        )
        experience_rows += (
            f'<article class="experience-card">'
            f'<div class="experience-card__meta">{_s(role.get("period", ""))} · {_s(role.get("location", ""))}</div>'
            f'<h3>{_s(role.get("title", ""))}</h3>'
            f'<div class="experience-card__org">{_s(role.get("organization", ""))}</div>'
            f"{details_html}"
            f"</article>"
        )
    experience_html = f"""
    <section class="section container reveal">
      <div class="about-split">
        <div>
          <div class="public-kicker">Experience</div>
          <h2 class="display-heading display-heading--section">The work history, chronologically.</h2>
          <div class="experience-stack">{experience_rows}</div>
        </div>
        <div class="about-side-photos">
          {_photo_tile(photos.get("work"), cls="editorial-photo--medium")}
          {_photo_tile(photos.get("home"), cls="editorial-photo--small")}
        </div>
      </div>
    </section>
    """

    education_html = "".join(
        f'<article class="education-card"><div class="education-card__years">{_s(item.get("years", ""))}</div>'
        f'<h3>{_s(item.get("school", ""))}</h3><div class="education-card__degree">{_s(item.get("degree", ""))}</div>'
        f'<p>{_s(item.get("details", ""))}</p></article>'
        for item in education
    )
    education_section = f"""
    <section class="section container reveal">
      <div class="about-split">
        <div>
          <div class="public-kicker">Education</div>
          <h2 class="display-heading display-heading--section">IIT Kharagpur and NYU Tandon are both in the wiring.</h2>
          <div class="education-grid">{education_html}</div>
        </div>
        <div class="about-side-photos">
          {_photo_tile(photos.get("ghibli_picnic"), cls="editorial-photo--medium")}
          {_photo_tile(photos.get("pokemon"), cls="editorial-photo--small")}
        </div>
      </div>
    </section>
    """

    skill_cards = "".join(
        f'<div class="skill-category"><div class="skill-category__label">{_s(item.get("category", ""))}</div>'
        f'{_pills(list(item.get("items") or []), cls="pill-list")}</div>'
        for item in skills
    )
    skills_section = f"""
    <section class="section container reveal">
      <div class="public-kicker">Skills</div>
      <h2 class="display-heading display-heading--section">The stack is wide because the work has been end to end.</h2>
      <div class="skills-grid">{skill_cards}</div>
    </section>
    """

    life_cards = "".join(
        f'<div class="life-detail"><strong>{_s(label)}</strong><br />{_s(value)}</div>'
        for label, value in [
            ("Home base", personal_signals.get("home_base") or p.get("location") or ""),
            ("Family", ", ".join(personal_signals.get("family") or [])),
            ("Languages", ", ".join(personal_signals.get("languages") or [])),
            ("Current signals", ", ".join((personal_signals.get("cultural_signals") or [])[:4])),
        ]
        if value
    )
    life_photo_keys = [
        "wedding",
        "couple",
        "cycling",
        "photo_break",
        "oscar_looking",
        "oscar_home",
        "skyline_2",
        "baking",
        "friends_brooklyn",
        "ghibli_cat",
        "ghibli_picnic",
        "pokemon",
        "oscar_selfie",
        "oscar_couch",
        "oscar_sploot",
        "oscar_office",
        "oscar_chair",
        "oscar_stairs",
        "oscar_window",
    ]
    life_gallery = "".join(
        _photo_tile(photos.get(key), cls="editorial-photo--small")
        for key in life_photo_keys[:8]
    )
    cat_gallery = "".join(
        _photo_tile(photos.get(key), cls="editorial-photo--small")
        for key in life_photo_keys[8:]
    )
    life_section = f"""
    <section class="section container reveal">
      <div class="public-kicker public-kicker--gold">Life</div>
      <h2 class="display-heading display-heading--section">Jersey City, Annie, Oscar, Iris, anime, comedy, music.</h2>
      <div class="life-details">{life_cards}</div>
      <div class="editorial-gallery editorial-gallery--editorial">{life_gallery}</div>
      <div class="editorial-gallery editorial-gallery--cats">{cat_gallery}</div>
    </section>
    """

    interests_html = _render_taste_modules(p)

    content = hero_html + arc_html + resume_overview_html + experience_html + education_section + skills_section + life_section + interests_html
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
    p = _payload(profile)
    name = _short_name(p)
    photos = p.get("photos") or {}
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
    flagship_projects, secondary_projects = _project_collections(projects)
    hero_html = f"""
    <section class="hero-inner">
      <div class="container">
        <div class="hero-inner__copy">
          <div class="public-kicker">Work</div>
          <h1 class="display-heading display-heading--hero">Flagship case studies first. Smaller proof after that.</h1>
          <p>The work page is ranked on purpose. The top four get full architecture, decision, struggle, and learning treatment. Everything else stays clearly framed as supporting proof.</p>
        </div>
        <div class="photo-accent--md">
          {_photo_img(photos.get("personality"), alt="Ahmad holding Oscar")}
        </div>
      </div>
    </section>
    """

    flagship_html = "".join(
        _render_flagship_showcase(project, rank=index)
        for index, project in enumerate(flagship_projects[:4])
    )
    flagship_section = f"""
    <section class="section container reveal">
      <div class="public-kicker">Flagship Projects</div>
      <h2 class="display-heading display-heading--section">The four projects with full case-study treatment.</h2>
      <div class="flagship-showcase-grid">{flagship_html}</div>
    </section>
    """

    secondary_html = "".join(_project_card_html(project) for project in secondary_projects[:2])
    secondary_section = (
        f"""
        <section class="section container reveal">
          <div class="public-kicker">Secondary Proof</div>
          <h2 class="display-heading display-heading--section">Useful evidence, but not pretending to be fuller than it is.</h2>
          <div class="project-grid">{secondary_html}</div>
        </section>
        """
        if secondary_html
        else ""
    )

    demo_cards = "".join(
        _demo_video_html(project, compact=False)
        for project in flagship_projects
        if _project_payload(project).get("demo_asset")
    )
    demo_section = (
        f"""
        <section class="section container reveal">
          <div class="public-kicker">Demo Library</div>
          <div class="demo-video-grid">{demo_cards}</div>
        </section>
        """
        if demo_cards
        else ""
    )

    cap_tags = "".join(
        f'<span class="capability-tag">{_s(item.get("title"))}</span>' for item in capabilities[:8]
    )
    cap_html = f"""
    <section class="section container reveal">
      <div class="public-kicker">Domains</div>
      <div class="capability-scroll">{cap_tags}</div>
    </section>
    """

    updates_html = _render_update_window(p.get("daily_update_window"))

    return hero_html + updates_html + flagship_section + secondary_section + demo_section + cap_html


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
    case_study = dict(proj_p.get("curated_case_study") or proj_p.get("case_study") or {})
    repo_history = dict(proj_p.get("repo_history") or {})
    freshness = dict(proj_p.get("freshness") or {})
    update_window = dict(proj_p.get("daily_update_window") or {})
    supporting_evidence = list(proj_p.get("supporting_evidence") or case_study.get("supporting_evidence") or [])
    appendix = dict(case_study.get("appendix") or {})
    link_html = "".join(
        f'<a class="inline-link" href="{_s(item.get("href"))}" target="_blank" rel="noreferrer">{_s(item.get("label") or "Open")}</a>'
        for item in list(proj_p.get("links") or [])
        if item.get("href")
    ) or "<span class='mono-accent'>Links coming soon.</span>"
    hero_html = f"""
    <section class="hero-inner">
      <div class="container detail-layout">
        <div>
          {_kicker("Case Study")}
          <h1 class="display-heading display-heading--section">{_s(project["title"])}</h1>
          <span class="status-badge">{_s(proj_p.get("status") or "Active")}</span>
          <p class="mt-2">{_s(proj_p.get("tagline") or project.get("summary") or "")}</p>
          <div class="freshness-pill mt-2">Last curated refresh: {_s(freshness.get("last_refreshed_at") or project.get("refreshed_at") or "Now")}</div>
        </div>
        <aside class="detail-sidebar">
          <div class="detail-sidebar__block">
            <h4>Stack</h4>
            {_pills(list(proj_p.get("stack") or [])[:10])}
          </div>
          <div class="detail-sidebar__block">
            <h4>Links</h4>
            <div class="link-column">{link_html}</div>
          </div>
        </aside>
      </div>
    </section>
    """

    framing_html = f"""
    <section class="section container reveal">
      {_kicker("Project Framing")}
      <div class="case-study-grid case-study-grid--framing case-study-grid--triple">
        <div class="case-study-panel">
          <h3>Project framing</h3>
          <p>{_s(case_study.get("project_framing") or project.get("summary") or "")}</p>
        </div>
        <div class="case-study-panel">
          <h3>Problem / Context</h3>
          <p>{_s(case_study.get("problem") or project.get("summary") or "")}</p>
        </div>
        <div class="case-study-panel">
          <h3>Role and Ownership</h3>
          <p>{_s(proj_p.get("role_scope") or "I owned the core technical and product decisions across this project.")}</p>
        </div>
      </div>
    </section>
    """

    constraints = list(proj_p.get("constraints") or case_study.get("constraints") or [])
    outcomes = list(case_study.get("outcomes") or proj_p.get("outcomes") or [])
    why_now = case_study.get("why_now") or ""
    summary_grid = f"""
    <section class="section container reveal">
      <div class="case-study-grid">
        <div class="case-study-panel"><h3>Why now</h3><p>{_s(why_now)}</p></div>
        <div class="case-study-panel"><h3>Constraints</h3>{_bullet_list(constraints)}</div>
        <div class="case-study-panel"><h3>Outcomes</h3>{_bullet_list(outcomes)}</div>
      </div>
    </section>
    """

    architecture_html = f"""
    <section class="section container reveal" id="architecture">
      {_kicker("Architecture")}
      <div class="case-study-panel case-study-panel--full">
        <h3>Architecture narrative</h3>
        <p>{_s(case_study.get("architecture_narrative") or "")}</p>
      </div>
      {_render_architecture_diagram(case_study.get("architecture_diagram"))}
    </section>
    """

    decisions_html = f"""
    <section class="section container reveal">
      {_kicker("Key Decisions")}
      {_render_decision_slider(list(case_study.get("key_decisions") or []))}
    </section>
    """

    phase_cards = "".join(
        f'<div class="case-study-panel"><div class="public-kicker">{_s(item.get("title", ""))}</div>'
        f'<p>{_s(item.get("summary", ""))}</p></div>'
        for item in list(case_study.get("iterations") or [])[:4]
    )
    phases_html = (
        f'<section class="section container reveal">{_kicker("Build Journey")}<div class="case-study-grid">{phase_cards}</div></section>'
        if phase_cards
        else ""
    )

    challenge_cards = "".join(
        f'<div class="case-study-panel"><h3>{_s(item.get("problem") or "")}</h3>'
        f'<p>{_s(item.get("resolution") or item.get("solution") or "")}</p></div>'
        for item in list(case_study.get("struggles") or [])[:6]
    )
    challenges_html = (
        f'<section class="section container reveal">{_kicker("Struggles")}<div class="case-study-grid">{challenge_cards}</div></section>'
        if challenge_cards
        else ""
    )

    learnings = list(case_study.get("learnings") or [])
    learning_cards = "".join(
        f'<div class="cs-learning"><span>{_s(item)}</span></div>' for item in learnings[:6]
    )
    learnings_html = (
        f'<section class="section container reveal">{_kicker("Learnings")}<div class="cs-learnings">{learning_cards}</div></section>'
        if learning_cards
        else ""
    )

    next_improvements = list(case_study.get("next_improvements") or [])
    next_steps_html = (
        f'<section class="section container reveal">{_kicker("Next Improvements")}<div class="case-study-panel case-study-panel--full">{_bullet_list(next_improvements)}</div></section>'
        if next_improvements
        else ""
    )

    metrics_rows = "".join(
        f'<div class="metric-row"><span>{_s(key.replace("-", " ").title())}</span><strong>{_s(value)}</strong></div>'
        for key, value in dict(appendix.get("metrics") or repo_history.get("code_metrics") or {}).items()
    )
    metrics_html = (
        f'<section class="section container reveal">{_kicker("Outcomes and Scale")}<div class="metrics-panel">{metrics_rows}</div></section>'
        if metrics_rows
        else ""
    )

    updates_html = (
        f'<section class="section container reveal">{_render_update_window(update_window)}</section>'
        if update_window.get("items")
        else ""
    )

    evidence_items = "".join(
        f'<div class="evidence-item"><strong>{_s(item.get("label") or "")}</strong>'
        f'<p>{_s(item.get("summary") or "")}</p></div>'
        for item in supporting_evidence[:5]
    )
    appendix_html = (
        f'<section class="section container reveal">{_kicker("Evidence Appendix")}<div class="evidence-appendix">{evidence_items}</div></section>'
        if evidence_items
        else ""
    )

    demo_file = proj_p.get("demo_asset") or ""
    media_html = (
        f'<section class="section container reveal" id="demo">{_kicker("Demo")}<div class="demo-video-container"><video class="demo-video" controls preload="metadata"><source src="/public-assets/profile/{_s(demo_file)}" type="video/mp4">Your browser does not support video.</video></div></section>'
        if demo_file
        else ""
    )

    content = (
        hero_html
        + framing_html
        + summary_grid
        + architecture_html
        + decisions_html
        + phases_html
        + challenges_html
        + learnings_html
        + metrics_html
        + updates_html
        + next_steps_html
        + appendix_html
        + media_html
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
    p = _payload(profile)
    name = _short_name(p)
    content = _render_brain_fallback(p, name, faq)

    page_script = ""
    captcha_enabled = public_chat_captcha_enabled()
    if captcha_enabled:
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
            page_script=page_script,
            body_class="public-page-open-brain",
            og_description=(
                "A conversational digital clone built from"
                " real evidence. Ask about Ahmad's work,"
                " projects, or fit."
            ),
            page_data={
                "page": "brain",
                "turnstileConfigured": captcha_enabled,
                "captchaEnabled": captcha_enabled,
                "chatEnabled": public_chat_enabled(),
            },
        )
    )


@router.get("/open-brain", response_class=HTMLResponse)
async def public_open_brain() -> HTMLResponse:
    """Redirect /open-brain to /brain for backward compat."""
    return RedirectResponse(url="/brain", status_code=301)


def _render_brain_fallback(p: dict, name: str, faq: list) -> str:
    thought_garden = list(p.get("thought_garden") or [])
    open_brain_topics = list(p.get("open_brain_topics") or [])
    captcha_enabled = public_chat_captcha_enabled()
    chat_live = public_chat_enabled()

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

    turnstile_widget = '<div id="turnstile-widget"></div>' if captcha_enabled else ""

    hero_html = f"""
    <section class="section container reveal">
      <div class="open-brain-hero open-brain-hero--page">
        <div>
          <div class="public-kicker">Open Brain</div>
          <h1 class="display-heading display-heading--hero">Ask the public-safe version of my brain.</h1>
          <p>It knows the approved version of my work, experience, projects, interests, and what I am trying to build. It does not have access to private memory, secrets, or unrelated assistant capabilities.</p>
          <div class="freshness-pill mt-2">{_s("Captcha enabled" if captcha_enabled else "No-captcha mode")} · {_s("Chat live" if chat_live else "Chat offline")}</div>
        </div>
        <div class="brain-topic-grid">
          {"".join(
              f'<div class="brain-topic-card"><h3>{_s(item.get("title", ""))}</h3><p>{_s(item.get("summary", ""))}</p></div>'
              for item in open_brain_topics[:4]
          )}
        </div>
      </div>
    </section>
    """

    scope_html = """
    <section class="section container reveal">
      <div class="case-study-grid">
        <div class="case-study-panel">
          <h3>What it knows</h3>
          <ul class="public-bullet-list">
            <li>My resume, work history, and current builder phase.</li>
            <li>Case-study level detail on duSraBheja, dataGenie, Balkan, and Kaffa.</li>
            <li>Public-safe life context like Jersey City, Annie, Oscar, Iris, music, anime, and comedy.</li>
          </ul>
        </div>
        <div class="case-study-panel">
          <h3>What it will not do</h3>
          <ul class="public-bullet-list">
            <li>Answer generic web questions or act like a normal assistant.</li>
            <li>Reveal private brain memory, secrets, credentials, or internal notes.</li>
            <li>Pretend certainty when the approved public evidence is thin.</li>
          </ul>
        </div>
      </div>
    </section>
    """

    chat_html = f"""
    <section class="section container">
      <div class="public-kicker">Chat</div>
      <h2 class="display-heading display-heading--section">Prompt it like a person, not like a search box.</h2>
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
            {"disabled" if not chat_live else ""}
          >Ask the clone</button>
          <div class="chat-footnote"
            data-public-chat-status>
            {_s("Multi-turn conversation. Ask follow-ups." if chat_live else "The public clone is temporarily offline.")}
          </div>
        </form>
      </div>
    </section>
    """
    how_html = """
    <section class="section container reveal">
      <div class="public-kicker">How it works</div>
      <div class="how-cards">
        <div class="how-card">
          <h4>Approved facts only</h4>
          <p>The public brain is grounded in a curated allowlist, not the raw private knowledge base.</p>
        </div>
        <div class="how-card">
          <h4>Multi-turn</h4>
          <p>You can ask follow-ups and push on details the same way you would in a real conversation.</p>
        </div>
        <div class="how-card">
          <h4>Evidence-led</h4>
          <p>It is designed to sound like me without drifting into generic filler or fake certainty.</p>
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

    return hero_html + scope_html + chat_html + how_html + faq_html + garden_html


# ──────────────────────────────────────────────
# Connect (new hybrid route) + Contact (compat)
# ──────────────────────────────────────────────


@router.get("/connect", response_class=HTMLResponse)
async def public_connect() -> HTMLResponse:
    async with async_session() as session:
        profile = await get_public_profile(session)
    p = _payload(profile)
    name = _short_name(p)
    photos = p.get("photos") or {}
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
    async with async_session() as session:
        ops = await get_public_surface_ops_status(session)
    return {
        "status": "ok",
        "site_title": settings.public_site_title,
        "chat_enabled": public_chat_enabled(),
        "captcha_enabled": public_chat_captcha_enabled(),
        "last_public_refresh_at": ops.get("last_public_refresh_at"),
        "latest_public_run_status": ops.get("latest_public_run_status"),
        "latest_wave_deploy_at": ops.get("latest_wave_deploy_at"),
    }
