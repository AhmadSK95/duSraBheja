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
    ("projects", "Work", "/work"),
    ("contact", "Connect", "/connect"),
    ("open-brain", "Open Brain", "/brain"),
)

OG_DEFAULTS = {
    "og:type": "website",
    "og:site_name": settings.public_site_title,
}
DEFAULT_SOCIAL_IMAGE = "buildwithmoenu_og_linkedin.jpg"
DEFAULT_SOCIAL_IMAGE_WIDTH = "1200"
DEFAULT_SOCIAL_IMAGE_HEIGHT = "630"


def _og_meta_tags(
    *,
    title: str,
    description: str = "",
    url: str = "",
    image: str = "",
) -> str:
    tags = {
        **OG_DEFAULTS,
        "og:title": title,
        "og:description": description,
        "twitter:card": "summary_large_image",
        "twitter:title": title,
        "twitter:description": description,
    }
    if url:
        tags["og:url"] = url
    if image:
        tags["og:image"] = image
        tags["og:image:width"] = DEFAULT_SOCIAL_IMAGE_WIDTH
        tags["og:image:height"] = DEFAULT_SOCIAL_IMAGE_HEIGHT
        tags["twitter:image"] = image
    lines = []
    for key, value in tags.items():
        if not value:
            continue
        attr = "name" if key.startswith("twitter:") else "property"
        lines.append(f'<meta {attr}="{key}" content="{html.escape(value)}" />')
    lines.append(f'<meta name="description" content="{html.escape(description)}" />')
    return "\n    ".join(lines)


def _footer_nav(active_nav: str) -> str:
    links = []
    for key, label, path in PUBLIC_NAV:
        cls = f"public-footer-link{' is-active' if key == active_nav else ''}"
        escaped = html.escape(label)
        links.append(f'<a class="{cls}" href="{path}">{escaped}</a>')
    return " ".join(links)


def render_public_shell(
    *,
    page_title: str,
    content_html: str,
    active_nav: str,
    page_data: dict | None = None,
    page_script: str = "",
    body_class: str = "",
    og_description: str = "",
    og_image: str = "",
    # Legacy params kept for backward compat — now unused by template
    hero_kicker: str = "",
    hero_title: str = "",
    hero_subtitle: str = "",
    hero_media_html: str = "",
) -> str:
    nav_html = []
    for key, label, path in PUBLIC_NAV:
        cls = f"public-nav-link{' is-active' if key == active_nav else ''}"
        escaped = html.escape(label)
        nav_html.append(f'<a class="{cls}" href="{path}">{escaped}</a>')

    base_url = (settings.public_base_url or "").rstrip("/")
    default_og_image = (
        f"{base_url}/public-assets/profile/{DEFAULT_SOCIAL_IMAGE}"
        if base_url
        else ""
    )
    og_meta = _og_meta_tags(
        title=page_title,
        description=og_description or hero_subtitle,
        url=base_url,
        image=og_image or default_og_image,
    )

    return SHELL_TEMPLATE.substitute(
        page_title=html.escape(page_title),
        og_meta_html=og_meta,
        site_title=html.escape(settings.public_site_title),
        site_host=html.escape(base_url.replace("https://", "").replace("http://", "")),
        nav_html="".join(nav_html),
        content_html=content_html,
        footer_nav_html=_footer_nav(active_nav),
        footer_admin_html='<a class="public-footer-admin" href="/admin">Dashboard</a>',
        footer_sig=html.escape(
            f"\u00a9 {settings.public_profile_name} \u00b7 Built with duSraBheja"
        ),
        page_data_json=html.escape(json.dumps(page_data or {}, ensure_ascii=False)),
        page_script=page_script,
        body_class=html.escape(body_class),
    )
