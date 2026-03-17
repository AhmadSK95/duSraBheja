"""Server-rendered public site shell."""

from __future__ import annotations

import html
import json
from pathlib import Path
from string import Template

from src.config import settings

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
SHELL_TEMPLATE = Template((TEMPLATE_DIR / "public_shell.html").read_text(encoding="utf-8"))

PUBLIC_NAV = (
    ("home", "Home", "/"),
    ("about", "About", "/about"),
    ("projects", "Projects", "/projects"),
    ("contact", "Contact", "/contact"),
    ("open-brain", "Open Brain", "/open-brain"),
)


def render_public_shell(
    *,
    page_title: str,
    hero_kicker: str,
    hero_title: str,
    hero_subtitle: str,
    hero_media_html: str = "",
    content_html: str,
    active_nav: str,
    page_data: dict | None = None,
    page_script: str = "",
    body_class: str = "",
) -> str:
    nav_html = []
    for key, label, path in PUBLIC_NAV:
        active_class = "is-active" if key == active_nav else ""
        nav_html.append(f'<a class="public-nav-link {active_class}" href="{path}">{html.escape(label)}</a>')
    return SHELL_TEMPLATE.substitute(
        page_title=html.escape(page_title),
        site_title=html.escape(settings.public_site_title),
        site_host=html.escape((settings.public_base_url or "").replace("https://", "").replace("http://", "").rstrip("/")),
        hero_kicker=html.escape(hero_kicker),
        hero_title=html.escape(hero_title),
        hero_subtitle=html.escape(hero_subtitle),
        hero_media_html=hero_media_html,
        nav_html="".join(nav_html),
        content_html=content_html,
        page_data_json=html.escape(json.dumps(page_data or {}, ensure_ascii=False)),
        page_script=page_script,
        body_class=html.escape(body_class),
    )
