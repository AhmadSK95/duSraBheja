"""Public-facing site and chatbot routes."""

from __future__ import annotations

import html

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from src.api.public_ui import render_public_shell
from src.api.schemas import PublicChatRequest
from src.config import settings
from src.database import async_session
from src.services.public_surface import (
    answer_public_question,
    get_public_profile,
    get_public_project,
    get_public_answer_policy,
    list_public_faq,
    list_public_projects,
)

router = APIRouter(tags=["public"])


def _profile_payload(profile: dict) -> dict:
    return dict(profile.get("payload") or {})


@router.get("/", response_class=HTMLResponse)
async def public_home() -> HTMLResponse:
    async with async_session() as session:
        profile = await get_public_profile(session)
        projects = await list_public_projects(session)
    payload = _profile_payload(profile)
    contact_rows = "".join(
        f'<a class="public-contact-pill" href="{html.escape(item["href"])}" target="_blank" rel="noreferrer">{html.escape(item["label"])}: {html.escape(item["value"])}</a>'
        for item in (payload.get("contact") or [])[:3]
        if item.get("href")
    )
    project_cards = "".join(
        f"""
        <article class="public-project-card">
          <div class="public-kicker">Selected Project</div>
          <h3>{html.escape(project["title"])}</h3>
          <p>{html.escape(project["summary"] or "")}</p>
          <a class="public-project-link" href="/projects/{html.escape(project['slug'])}">Open the case study</a>
        </article>
        """
        for project in projects[:4]
    )
    interest_tags = "".join(
        f'<span class="public-tag">{html.escape(item)}</span>'
        for item in (payload.get("interests") or [])[:6]
    )
    content = f"""
    <div class="public-grid two">
      <section class="public-card">
        <div class="public-kicker">Living Profile</div>
        <h2>{html.escape(settings.public_profile_name)}</h2>
        <p>{html.escape(payload.get("identity") or profile.get("summary") or "")}</p>
        <div class="public-tags">{interest_tags}</div>
      </section>
      <section class="public-card">
        <div class="public-kicker">How I Work</div>
        <ul class="public-list">
          {''.join(f'<li>{html.escape(item)}</li>' for item in (payload.get("skills") or [])[:4])}
        </ul>
        <div class="public-pill-row">{contact_rows}</div>
      </section>
    </div>
    <div class="public-divider"></div>
    <section class="public-grid two">
      {project_cards}
    </section>
    """
    html_body = render_public_shell(
        page_title=f"{settings.public_profile_short_name} — Open Brain",
        hero_kicker="Open Brain",
        hero_title=f"{settings.public_profile_short_name} in public.",
        hero_subtitle=payload.get("hero_summary") or profile.get("summary") or "A living profile shaped by approved facts from Ahmad's brain.",
        content_html=content,
        active_nav="home",
        page_data={"page": "home"},
    )
    return HTMLResponse(html_body)


@router.get("/about", response_class=HTMLResponse)
async def public_about() -> HTMLResponse:
    async with async_session() as session:
        profile = await get_public_profile(session)
    payload = _profile_payload(profile)
    content = f"""
    <div class="public-grid two">
      <section class="public-card">
        <div class="public-kicker">Profile</div>
        <h2>Who Ahmad is</h2>
        <p>{html.escape(payload.get("identity") or "")}</p>
      </section>
      <section class="public-card">
        <div class="public-kicker">Current Focus</div>
        <ul class="public-list">
          {''.join(f'<li>{html.escape(item)}</li>' for item in (payload.get("current_focus") or [])[:4])}
        </ul>
      </section>
    </div>
    <div class="public-grid two">
      <section class="public-card">
        <div class="public-kicker">Experience</div>
        <ul class="public-list">
          {''.join(f'<li>{html.escape(item)}</li>' for item in (payload.get("experience") or [])[:6])}
        </ul>
      </section>
      <section class="public-card">
        <div class="public-kicker">Education + Taste</div>
        <ul class="public-list">
          {''.join(f'<li>{html.escape(item)}</li>' for item in (payload.get("education") or [])[:4])}
          {''.join(f'<li>{html.escape(item)}</li>' for item in (payload.get("interests") or [])[:4])}
        </ul>
      </section>
    </div>
    """
    return HTMLResponse(
        render_public_shell(
            page_title=f"About {settings.public_profile_short_name}",
            hero_kicker="About",
            hero_title=f"{settings.public_profile_short_name}, as told by approved facts.",
            hero_subtitle=profile.get("summary") or "",
            content_html=content,
            active_nav="about",
            page_data={"page": "about", "profile": profile},
        )
    )


@router.get("/contact", response_class=HTMLResponse)
async def public_contact() -> HTMLResponse:
    async with async_session() as session:
        profile = await get_public_profile(session)
    payload = _profile_payload(profile)
    contact_cards = "".join(
        f"""
        <article class="public-card">
          <div class="public-kicker">{html.escape(item.get("label") or "Contact")}</div>
          <h3>{html.escape(item.get("value") or "")}</h3>
          <a class="public-project-link" href="{html.escape(item.get("href") or "#")}" target="_blank" rel="noreferrer">Reach out</a>
        </article>
        """
        for item in (payload.get("contact") or [])
        if item.get("href")
    ) or '<article class="public-card"><div class="public-kicker">Contact</div><h3>Contact details are being curated.</h3></article>'
    content = f"""
    <div class="public-grid two">
      <section class="public-card">
        <div class="public-kicker">Let’s Talk</div>
        <h2>Reach out directly.</h2>
        <p>If you want to collaborate, hire, or just compare notes on systems, products, and AI, these are the cleanest channels.</p>
      </section>
      <section class="public-card">
        <div class="public-kicker">Location</div>
        <h2>{html.escape(payload.get("location") or settings.public_profile_location)}</h2>
        <p>{html.escape(profile.get("summary") or "")}</p>
      </section>
    </div>
    <div class="public-divider"></div>
    <section class="public-grid two">{contact_cards}</section>
    """
    return HTMLResponse(
        render_public_shell(
            page_title=f"Contact {settings.public_profile_short_name}",
            hero_kicker="Contact",
            hero_title="A clean way to reach Ahmad.",
            hero_subtitle="Public-facing contact lanes only. Private brain, vault, and admin surfaces stay behind login.",
            content_html=content,
            active_nav="contact",
            page_data={"page": "contact", "profile": profile},
        )
    )


@router.get("/projects", response_class=HTMLResponse)
async def public_projects() -> HTMLResponse:
    async with async_session() as session:
        projects = await list_public_projects(session)
    cards = "".join(
        f"""
        <article class="public-project-card">
          <div class="public-kicker">Case Study</div>
          <h3>{html.escape(project["title"])}</h3>
          <p>{html.escape(project["summary"] or "")}</p>
          <a class="public-project-link" href="/projects/{html.escape(project['slug'])}">See the engineering story</a>
        </article>
        """
        for project in projects
    )
    return HTMLResponse(
        render_public_shell(
            page_title=f"{settings.public_profile_short_name} Projects",
            hero_kicker="Projects",
            hero_title="Products, systems, and client work.",
            hero_subtitle="A conference-floor view of what Ahmad builds: why it exists, how it was designed, and what it demonstrates.",
            content_html=f'<section class="public-grid two">{cards}</section>',
            active_nav="projects",
            page_data={"page": "projects", "projects": projects},
        )
    )


@router.get("/projects/{slug}", response_class=HTMLResponse)
async def public_project_detail(slug: str) -> HTMLResponse:
    async with async_session() as session:
        project = await get_public_project(session, slug)
    if not project:
        raise HTTPException(status_code=404, detail="Public project not found")
    payload = dict(project.get("payload") or {})
    signals = "".join(
        f"""
        <article class="public-card">
          <div class="public-kicker">{html.escape(signal.get("fact_type") or "signal")}</div>
          <h3>{html.escape(signal.get("title") or project["title"])}</h3>
          <p>{html.escape(signal.get("body") or "")}</p>
          <div class="public-meta">{html.escape(signal.get("updated_at") or "")}</div>
        </article>
        """
        for signal in payload.get("signals") or []
    )
    content = f"""
    <section class="public-project-detail">
      <article class="public-card">
        <div class="public-kicker">Project Overview</div>
        <h2>{html.escape(project['title'])}</h2>
        <p>{html.escape(payload.get('summary') or project.get('summary') or '')}</p>
      </article>
      <section class="public-grid two">{signals}</section>
    </section>
    """
    return HTMLResponse(
        render_public_shell(
            page_title=project["title"],
            hero_kicker="Project",
            hero_title=project["title"],
            hero_subtitle=project.get("summary") or "",
            content_html=content,
            active_nav="projects",
            page_data={"page": "project-detail", "project": project},
        )
    )


@router.get("/open-brain", response_class=HTMLResponse)
async def public_open_brain() -> HTMLResponse:
    async with async_session() as session:
        faq = await list_public_faq(session)
    faq_html = "".join(
        f"""
        <article class="public-card">
          <div class="public-kicker">FAQ</div>
          <h3>{html.escape(item["question"])}</h3>
          <p>{html.escape(item["answer"])}</p>
        </article>
        """
        for item in faq
    )
    turnstile_configured = bool(settings.cloudflare_turnstile_site_key and settings.cloudflare_turnstile_secret_key)
    content = f"""
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
    <div class="public-divider"></div>
    <section class="public-grid two">{faq_html}</section>
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
            hero_subtitle="A curated profile bot for recruiters, collaborators, and curious humans. It only answers from approved public facts.",
            content_html=content,
            active_nav="open-brain",
            page_data={"page": "open-brain", "turnstileConfigured": turnstile_configured},
            page_script=page_script,
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
