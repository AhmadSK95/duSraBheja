from __future__ import annotations

from src.api.dashboard_ui import render_dashboard_shell
from src.api.routes import dashboard as dashboard_routes


def test_dashboard_shell_renders_grouped_navigation_and_utility_links() -> None:
    response = render_dashboard_shell(
        title="Overview",
        token="demo",
        active_page="overview",
        hero_kicker="Narrative View",
        hero_title="Command Center",
        hero_subtitle="Private operating surface.",
        content_html="<div>content</div>",
        utility_html='<a class="atlas-utility-chip" href="/dashboard/public-surface?token=demo"><span>Public Surface Ops</span></a>',
    )
    text = response.body.decode("utf-8")

    assert "Command Center" in text
    assert "Brain Views" in text
    assert "Deep Ops" in text
    assert "Automation" in text
    assert "/connect" in text
    assert "Public Surface Ops" in text


def test_dashboard_page_helper_uses_shell_and_strips_legacy_heading() -> None:
    response = dashboard_routes._page(
        "Artifacts",
        "<h1>Artifact Intake</h1><p>Latest stored captures with their current interpretation and review status.</p><table><tbody><tr><td>row</td></tr></tbody></table>",
        token="",
    )
    text = response.body.decode("utf-8")

    assert "atlas-app-shell" in text
    assert "Artifacts" in text
    assert "Latest stored captures with their current interpretation and review status." in text
    assert "<table>" in text
