"""Lightweight file-backed dashboard shell rendering."""

from __future__ import annotations

import html
from pathlib import Path
from string import Template

from fastapi.responses import HTMLResponse

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
SHELL_TEMPLATE = Template((TEMPLATE_DIR / "dashboard_shell.html").read_text(encoding="utf-8"))

NAV_ITEMS = [
    ("overview", "Overview", "/dashboard/overview"),
    ("timeline", "Timeline", "/dashboard/timeline"),
    ("expertise", "Expertise", "/dashboard/expertise"),
    ("projects", "Projects", "/dashboard/projects"),
    ("public-surface", "Public Surface", "/dashboard/public-surface"),
    ("sources", "Sources", "/dashboard/sources"),
    ("coverage", "Coverage", "/dashboard/coverage"),
    ("library", "Library", "/dashboard/library"),
]


def dashboard_url(path: str, token: str = "") -> str:
    safe_token = html.escape(token)
    if not safe_token:
        return path
    joiner = "&" if "?" in path else "?"
    return f"{path}{joiner}token={safe_token}"


def render_dashboard_shell(
    *,
    title: str,
    token: str,
    active_page: str,
    hero_kicker: str,
    hero_title: str,
    hero_subtitle: str,
    content_html: str,
    page_data_json: str = "null",
    page_script: str = "",
    logout_html: str = "",
) -> HTMLResponse:
    nav_html = []
    for slug, label, path in NAV_ITEMS:
        is_active = "is-active" if slug == active_page else ""
        nav_html.append(
            f'<a class="atlas-nav-link {is_active}" href="{dashboard_url(path, token)}">{html.escape(label)}</a>'
        )
    if not logout_html:
        logout_html = (
            '<form class="atlas-logout" method="post" action="/dashboard/logout">'
            '<button class="atlas-nav-link atlas-nav-link--logout" type="submit">Log out</button>'
            "</form>"
        )
    body = SHELL_TEMPLATE.safe_substitute(
        page_title=html.escape(title),
        nav_html="".join(nav_html),
        hero_kicker=html.escape(hero_kicker),
        hero_title=html.escape(hero_title),
        hero_subtitle=html.escape(hero_subtitle),
        content_html=content_html,
        page_data_json=page_data_json,
        page_script=page_script,
        logout_html=logout_html,
    )
    return HTMLResponse(body)
