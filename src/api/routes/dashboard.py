"""Private dashboard and moderation routes."""

from __future__ import annotations

import html
import json
import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from src.api.dashboard_ui import dashboard_url, render_dashboard_shell
from src.api.schemas import ArtifactModerationRequest, BoardRegenerateRequest, EvalRunRequest
from src.constants import normalize_category
from src.database import async_session
from src.lib import store
from src.lib.auth import (
    dashboard_credentials_match,
    dashboard_username,
    require_api_token,
    require_dashboard_token,
)
from src.lib.time import format_display_datetime
from src.services.boards import daily_board_window, generate_or_refresh_board, weekly_board_window
from src.services.brain_atlas import build_brain_atlas_snapshot
from src.services.brain_os import build_brain_self_description
from src.services.capture_analysis import normalize_capture_intent, normalize_validation_status
from src.services.digest import generate_or_refresh_digest
from src.services.evaluation import run_query_eval
from src.services.library import build_final_stored_data, build_library_catalog
from src.services.library_cleanup import build_library_cleanup_preview
from src.services.profile_narrative import materialize_profile_read_models
from src.services.project_state import recompute_project_states
from src.services.public_surface import (
    approve_product_improvement_wave,
    get_public_surface_ops_status,
    list_public_facts,
    refresh_public_snapshots,
    run_product_improvement_cycle,
    run_public_surface_refresh,
    seed_public_facts_from_interview_prep,
    update_public_fact,
)
from src.services.secrets import build_secret_inventory
from src.worker.main import JOB_GENERATE_EMBEDDINGS, JOB_PROCESS_LIBRARIAN, get_pool

router = APIRouter(tags=["dashboard"])
api_router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _login_page(*, next_path: str, error: str | None = None) -> HTMLResponse:
    safe_next = html.escape(next_path or "/dashboard/overview")
    error_html = f'<div class="atlas-login-error">{html.escape(error)}</div>' if error else ""
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Brain Login</title>
    <link rel="stylesheet" href="/static/dashboard/brain_atlas.css" />
  </head>
  <body class="atlas-body atlas-body--login">
    <main class="atlas-login-shell">
      <section class="atlas-login-card">
        <div class="atlas-kicker">Private Brain</div>
        <h1>Sign in to Brain Atlas</h1>
        <p>Private access only. The dashboard stays behind a login even if someone guesses the URL.</p>
        {error_html}
        <form method="post" action="/dashboard/login" class="atlas-login-form">
          <input type="hidden" name="next" value="{safe_next}" />
          <label>
            <span>Username</span>
            <input type="text" name="username" autocomplete="username" value="{html.escape(dashboard_username())}" required />
          </label>
          <label>
            <span>Password</span>
            <input type="password" name="password" autocomplete="current-password" required />
          </label>
          <button type="submit">Open Atlas</button>
        </form>
      </section>
    </main>
  </body>
</html>"""
    )


def _fmt_dt(value) -> str:
    return format_display_datetime(value)


def _pill(text: str, *, warm: bool = False) -> str:
    classes = "atlas-pill atlas-pill--warm" if warm else "atlas-pill"
    return f'<span class="{classes}">{html.escape(text)}</span>'


def _meta_line(values: list[str]) -> str:
    return '<div class="atlas-meta">' + "".join(f"<span>{html.escape(value)}</span>" for value in values if value) + "</div>"


def _list_item(title: str, summary: str, *, meta: list[str] | None = None) -> str:
    meta_html = _meta_line(meta or [])
    return (
        '<div class="atlas-list-item">'
        f"<strong>{html.escape(title)}</strong>"
        f"<div>{html.escape(summary)}</div>"
        f"{meta_html}"
        "</div>"
    )


def _serialize_json(payload: object) -> str:
    return json.dumps(payload, default=str).replace("</", "<\\/")


async def _public_fact_payloads(session) -> list[dict]:
    facts = await list_public_facts(session, limit=300)
    return [
        {
            "id": str(fact.id),
            "title": fact.title,
            "body": fact.body,
            "fact_type": fact.fact_type,
            "facet": fact.facet,
            "approved": fact.approved,
            "project_slug": fact.project_slug,
            "updated_at": _fmt_dt(fact.updated_at),
        }
        for fact in facts
    ]


def _render_overview_page(payload: dict, *, token: str) -> HTMLResponse:
    identity = list(payload.get("identity_stack") or [])
    metrics = list(payload.get("key_metrics") or [])
    projects = list(payload.get("flagship_projects") or [])
    current_arc = dict(payload.get("current_arc") or {})
    cards_html = "".join(
        _list_item(
            item.get("title") or "Project",
            item.get("summary") or item.get("tagline") or "",
            meta=[item.get("status") or "", item.get("slug") or ""],
        )
        for item in projects
    ) or '<div class="atlas-empty">No curated projects in the overview model yet.</div>'
    content_html = f"""
    <div class="atlas-grid">
      <section class="atlas-card atlas-card--span-12">
        <div class="atlas-stat-row">
          {''.join(_metric(item.get('label') or 'Metric', str(item.get('value') or '0')) for item in metrics)}
        </div>
      </section>
      <section class="atlas-card atlas-card--span-7">
        <h2>Identity Stack</h2>
        <div class="atlas-list">{''.join(_list_item(f'Layer {index}', line, meta=[]) for index, line in enumerate(identity, start=1))}</div>
      </section>
      <section class="atlas-card atlas-card--span-5">
        <h2>Current Arc</h2>
        <p>{html.escape(current_arc.get('summary') or payload.get('summary') or '')}</p>
        <div class="atlas-list">
          {''.join(_list_item('Focus', item, meta=[]) for item in list(current_arc.get('focus') or []))}
        </div>
      </section>
      <section class="atlas-card atlas-card--span-12">
        <h2>Flagship Projects</h2>
        <div class="atlas-list">{cards_html}</div>
      </section>
    </div>
    """
    return render_dashboard_shell(
        title="Overview",
        token=token,
        active_page="overview",
        hero_kicker="Narrative View",
        hero_title="Profile Overview",
        hero_subtitle=payload.get("headline") or "A curated overview of identity, current arc, and flagship work.",
        content_html=content_html,
    )


def _render_timeline_page(payload: dict, *, token: str) -> HTMLResponse:
    eras = list(payload.get("eras") or [])
    events = list(payload.get("events") or [])
    eras_html = "".join(
        _list_item(
            item.get("title") or "Era",
            item.get("summary") or "",
            meta=[item.get("years") or "", ", ".join(item.get("institutions") or [])],
        )
        for item in eras
    ) or '<div class="atlas-empty">No eras in the timeline model yet.</div>'
    events_html = "".join(
        _list_item(item.get("year") or "Year", item.get("event") or "", meta=[])
        for item in events
    ) or '<div class="atlas-empty">No timeline events available.</div>'
    content_html = f"""
    <div class="atlas-grid">
      <section class="atlas-card atlas-card--span-6">
        <h2>Life Eras</h2>
        <div class="atlas-list">{eras_html}</div>
      </section>
      <section class="atlas-card atlas-card--span-6">
        <h2>Timeline Events</h2>
        <div class="atlas-list">{events_html}</div>
      </section>
    </div>
    """
    return render_dashboard_shell(
        title="Timeline",
        token=token,
        active_page="timeline",
        hero_kicker="Chaptered Biography",
        hero_title="Timeline",
        hero_subtitle="The private chapter model from IIT KGP through NYU, Amazon, and the builder phase.",
        content_html=content_html,
    )


def _render_expertise_page(payload: dict, *, token: str) -> HTMLResponse:
    books = list(payload.get("books") or [])
    mapping = dict(payload.get("library_mapping") or {})
    books_html = "".join(
        _list_item(
            item.get("title") or "Capability",
            item.get("summary") or "",
            meta=[item.get("slug") or "", f"chapters={len(item.get('chapters') or [])}"],
        )
        for item in books
    ) or '<div class="atlas-empty">No expertise books materialized yet.</div>'
    mapping_html = "".join(_list_item(key.title(), value, meta=[]) for key, value in mapping.items())
    content_html = f"""
    <div class="atlas-grid">
      <section class="atlas-card atlas-card--span-7">
        <h2>Expertise Books</h2>
        <div class="atlas-list">{books_html}</div>
      </section>
      <section class="atlas-card atlas-card--span-5">
        <h2>Library Metaphor</h2>
        <div class="atlas-list">{mapping_html}</div>
      </section>
    </div>
    """
    return render_dashboard_shell(
        title="Expertise",
        token=token,
        active_page="expertise",
        hero_kicker="Capability Layer",
        hero_title="Expertise",
        hero_subtitle="Domains and capabilities that convert raw signals into stable expertise narratives.",
        content_html=content_html,
    )


def _render_profile_projects_page(payload: dict, *, token: str) -> HTMLResponse:
    items = list(payload.get("items") or [])
    cards_html = "".join(
        _list_item(
            item.get("title") or "Project",
            item.get("summary") or item.get("tagline") or "",
            meta=[item.get("status") or "", ", ".join(item.get("stack") or [])[:80]],
        )
        for item in items
    ) or '<div class="atlas-empty">No curated project cases yet.</div>'
    content_html = f"""
    <div class="atlas-grid">
      <section class="atlas-card atlas-card--span-12">
        <h2>Project Cases</h2>
        <p>These cases are sourced from CompanyInterviewPrep narrative files, not generated from stale atlas facets.</p>
        <div class="atlas-list">{cards_html}</div>
      </section>
    </div>
    """
    return render_dashboard_shell(
        title="Projects",
        token=token,
        active_page="projects",
        hero_kicker="Proof-Rich Cases",
        hero_title="Projects",
        hero_subtitle="Case studies grounded in explicit narrative sources and evidence framing.",
        content_html=content_html,
    )


def _render_sources_page(payload: dict, *, token: str) -> HTMLResponse:
    source_pack = dict(payload.get("seed_pack") or {})
    inventory = dict(payload.get("inventory") or {})
    live_counts = list(payload.get("live_source_counts") or [])
    advice = list(payload.get("advice") or [])
    files_html = "".join(
        _list_item("Seed file", item, meta=[])
        for item in list(source_pack.get("files") or [])
    ) or '<div class="atlas-empty">No source-pack files configured.</div>'
    counts_html = "".join(
        _list_item(item.get("source_type") or "source", str(item.get("items") or 0), meta=["items"])
        for item in live_counts
    ) or '<div class="atlas-empty">No live source counts available.</div>'
    roots_html = "".join(
        _list_item("Scanned root", item, meta=[])
        for item in list(inventory.get("roots_scanned") or [])[:8]
    ) or '<div class="atlas-empty">No inventory roots scanned yet.</div>'
    advice_html = "".join(_list_item("Guidance", item, meta=[]) for item in advice)
    content_html = f"""
    <div class="atlas-grid">
      <section class="atlas-card atlas-card--span-6">
        <h2>Narrative Source Pack</h2>
        <div class="atlas-list">{files_html}</div>
      </section>
      <section class="atlas-card atlas-card--span-6">
        <h2>Live Source Counts</h2>
        <div class="atlas-list">{counts_html}</div>
      </section>
      <section class="atlas-card atlas-card--span-12">
        <h2>Inventory Roots</h2>
        <div class="atlas-list">{roots_html}</div>
      </section>
      <section class="atlas-card atlas-card--span-12">
        <h2>Ingestion Guidance</h2>
        <div class="atlas-list">{advice_html}</div>
      </section>
    </div>
    """
    return render_dashboard_shell(
        title="Sources",
        token=token,
        active_page="sources",
        hero_kicker="Data Inputs",
        hero_title="Sources",
        hero_subtitle="Where the biography and expertise layers come from, and which feeds remain thin.",
        content_html=content_html,
    )


def _render_coverage_page(payload: dict, *, token: str) -> HTMLResponse:
    gaps = list(payload.get("gaps") or [])
    chapters = list(payload.get("expected_chapters") or [])
    institution_hits = dict(payload.get("institution_hits") or {})
    inventory = dict(payload.get("inventory") or {})
    imports = list(inventory.get("recommended_next_imports") or [])
    gaps_html = "".join(
        _list_item(
            item.get("title") or "Coverage gap",
            item.get("summary") or "",
            meta=[item.get("severity") or "", item.get("recommendation") or ""],
        )
        for item in gaps
    ) or '<div class="atlas-empty">No gaps currently flagged.</div>'
    chapters_html = "".join(_list_item("Chapter", item, meta=[]) for item in chapters)
    hits_html = "".join(
        _list_item(key.upper(), json.dumps(value), meta=["keyword coverage"])
        for key, value in institution_hits.items()
    ) or '<div class="atlas-empty">No institution hit metrics available.</div>'
    imports_html = "".join(_list_item("Import candidate", item, meta=[]) for item in imports[:8]) or '<div class="atlas-empty">No import candidates detected yet.</div>'
    content_html = f"""
    <div class="atlas-grid">
      <section class="atlas-card atlas-card--span-7">
        <h2>Coverage Gaps</h2>
        <div class="atlas-list">{gaps_html}</div>
      </section>
      <section class="atlas-card atlas-card--span-5">
        <h2>Expected Chapters</h2>
        <div class="atlas-list">{chapters_html}</div>
      </section>
      <section class="atlas-card atlas-card--span-12">
        <h2>Institution Hit Metrics</h2>
        <div class="atlas-list">{hits_html}</div>
      </section>
      <section class="atlas-card atlas-card--span-12">
        <h2>Recommended Imports</h2>
        <div class="atlas-list">{imports_html}</div>
      </section>
    </div>
    """
    return render_dashboard_shell(
        title="Coverage",
        token=token,
        active_page="coverage",
        hero_kicker="Data Quality",
        hero_title="Coverage",
        hero_subtitle="Gaps and chapter coverage so we can strengthen memory quality systematically.",
        content_html=content_html,
    )


def _page(title: str, body: str, *, token: str) -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>{html.escape(title)}</title>
    <style>
      body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 24px; background: #f7f5ef; color: #1f2937; }}
      a {{ color: #0f766e; text-decoration: none; }}
      table {{ width: 100%; border-collapse: collapse; margin-top: 16px; background: white; }}
      th, td {{ border: 1px solid #d6d3d1; padding: 10px; vertical-align: top; text-align: left; }}
      th {{ background: #f1f5f9; }}
      .nav a {{ margin-right: 16px; font-weight: 600; }}
      .pill {{ display: inline-block; padding: 2px 8px; border-radius: 999px; background: #e2e8f0; margin-right: 6px; font-size: 12px; }}
      pre {{ white-space: pre-wrap; background: #111827; color: #f9fafb; padding: 12px; border-radius: 8px; }}
    </style>
  </head>
  <body>
    <div class="nav">
      <a href="{dashboard_url('/dashboard/atlas', token)}">Atlas</a>
      <a href="{dashboard_url('/dashboard/story-river', token)}">Story River</a>
      <a href="{dashboard_url('/dashboard/library', token)}">Library</a>
      <a href="{dashboard_url('/dashboard/projects', token)}">Projects</a>
      <a href="{dashboard_url('/dashboard/media', token)}">Media</a>
      <a href="{dashboard_url('/dashboard/subconscious', token)}">Subconscious</a>
      <a href="{dashboard_url('/dashboard/health', token)}">Health</a>
      <a href="{dashboard_url('/dashboard/artifacts', token)}">Artifacts</a>
      <a href="{dashboard_url('/dashboard/notes', token)}">Notes</a>
      <a href="{dashboard_url('/dashboard/chrome-signals', token)}">Chrome Signals</a>
      <a href="{dashboard_url('/dashboard/review', token)}">Review</a>
      <a href="{dashboard_url('/dashboard/boards', token)}">Boards</a>
      <a href="{dashboard_url('/dashboard/query-traces', token)}">Query Traces</a>
      <a href="{dashboard_url('/dashboard/evals', token)}">Evals</a>
      <a href="{dashboard_url('/dashboard/sync-health', token)}">Sync Health</a>
    </div>
    {body}
  </body>
</html>"""
    )


def _render_artifact_rows(items: list[dict], token: str) -> str:
    rows = []
    for item in items:
        artifact = item["artifact"]
        issues = ", ".join(issue.get("code", "issue") for issue in item.get("quality_issues", [])) or "none"
        rows.append(
            "<tr>"
            f"<td><a href=\"{dashboard_url(f'/dashboard/artifacts/{artifact.id}', token)}\">{str(artifact.id)[:8]}</a></td>"
            f"<td>{html.escape(artifact.source)}</td>"
            f"<td>{html.escape(item.get('category') or 'unclassified')}</td>"
            f"<td>{html.escape(item.get('capture_intent') or 'unknown')}</td>"
            f"<td>{html.escape(item.get('validation_status') or 'unknown')}</td>"
            f"<td>{html.escape(issues)}</td>"
            f"<td>{_fmt_dt(artifact.created_at)}</td>"
            "</tr>"
        )
    return "".join(rows)


@router.get("/dashboard/login", response_class=HTMLResponse)
async def dashboard_login(next: str = Query(default="/dashboard/overview")) -> HTMLResponse:
    return _login_page(next_path=next)


@router.post("/dashboard/login", response_class=HTMLResponse)
async def dashboard_login_submit(
    request: Request,
    username: str = Form(default=""),
    password: str = Form(default=""),
    next: str = Form(default="/dashboard/overview"),
) -> Response:
    if not dashboard_credentials_match(username=username, password=password):
        return _login_page(next_path=next, error="That login didn’t match the private dashboard credentials.")
    request.session["dashboard_authenticated"] = True
    request.session["dashboard_username"] = dashboard_username()
    destination = next if next.startswith("/dashboard") else "/dashboard/overview"
    return RedirectResponse(url=destination, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/dashboard/logout")
async def dashboard_logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse(url="/dashboard/login", status_code=status.HTTP_303_SEE_OTHER)


def _metric(label: str, value: str) -> str:
    return (
        '<div class="atlas-metric">'
        f'<div class="atlas-metric-label">{html.escape(label)}</div>'
        f'<div class="atlas-metric-value">{html.escape(value)}</div>'
        "</div>"
    )


def _render_atlas_page(snapshot: dict, *, token: str) -> HTMLResponse:
    facets = list(snapshot.get("facets") or [])
    story_river = list(snapshot.get("story_river") or [])
    subconscious = list(snapshot.get("subconscious") or [])
    current_headspace = list(snapshot.get("current_headspace") or [])
    memory_paths = list(snapshot.get("memory_paths") or [])
    library_preview = list(snapshot.get("library_preview") or [])
    health = dict(snapshot.get("health") or {})
    top_facets = facets[:6]
    highlight_items_html = "".join(
        _list_item(
            item.get("title") or "Facet",
            item.get("summary") or "",
            meta=[
                item.get("facet_type") or "",
                item.get("happened_at_local") or "",
                f"path {float((item.get('metadata') or {}).get('path_score') or 0.0):.2f}",
            ],
        )
        for item in top_facets
    )
    content_html = f"""
    <div class="atlas-grid">
      <section class="atlas-card atlas-card--span-12">
        <div class="atlas-stat-row">
          {_metric('Facets', str(health.get('facet_count', len(facets))))}
          {_metric('Projects', str(health.get('project_count', 0)))}
          {_metric('Current Headspace', str(health.get('current_headspace_count', len(current_headspace))))}
          {_metric('Memory Paths', str(health.get('memory_path_count', len(memory_paths))))}
          {_metric('Review Queue', str(health.get('pending_review_count', 0)))}
          {_metric('Trace Failures', str(health.get('recent_trace_failures', 0)))}
        </div>
      </section>
      <section class="atlas-card atlas-card--span-12">
        <div class="atlas-atlas-shell">
          <div class="atlas-card atlas-map-wrap">
            <div class="atlas-section-title">Cognitive Map</div>
            <div class="atlas-map" data-atlas-map></div>
          </div>
          <aside class="atlas-detail" data-atlas-detail>
            <div class="atlas-panel-card"><div class="atlas-empty">Select a node to inspect its evidence, story, and open loops.</div></div>
          </aside>
        </div>
      </section>
      <section class="atlas-card atlas-card--span-6">
        <h2>Current Headspace</h2>
        <p>The nodes that are genuinely closest to the surface right now because recent memory paths keep running through them.</p>
        <div class="atlas-list">
          {''.join(_list_item(item.get('title') or 'Facet', item.get('summary') or '', meta=[item.get('facet_type') or '', f"path {float(item.get('path_score') or 0.0):.2f}", item.get('happened_at_local') or '']) for item in current_headspace[:6])}
        </div>
      </section>
      <section class="atlas-card atlas-card--span-6">
        <h2>Memory Paths</h2>
        <p>Time-aware traversals from recent anchors into the rest of the brain. This is the layer that should make recency emerge naturally instead of being painted on later.</p>
        <div class="atlas-list">
          {''.join(_list_item(item.get('title') or 'Path', item.get('summary') or '', meta=[item.get('provenance') or '', f"path {float(item.get('path_score') or 0.0):.2f}", item.get('anchor_time_local') or '']) for item in memory_paths[:5])}
        </div>
      </section>
      <section class="atlas-card atlas-card--span-5">
        <h2>Quiet Subconscious</h2>
        <p>Replay, map, dream, and foresight outputs stay quiet here and feed the digest, boards, and agent bootstraps when they matter.</p>
        <div class="atlas-list">
          {''.join(_list_item(item.get('title') or 'Insight', item.get('summary') or '', meta=[item.get('lane') or '', item.get('certainty') or '']) for item in subconscious[:4])}
        </div>
      </section>
      <section class="atlas-card atlas-card--span-7">
        <h2>Atlas Highlights</h2>
        <p>The strongest active clusters across the whole brain after headspace traversal, curation, and direct evidence weighting.</p>
        <div class="atlas-list">
          {highlight_items_html}
        </div>
      </section>
      <section class="atlas-card atlas-card--span-6">
        <h2>Story River</h2>
        <p>Major boards and sessions flowing through the current narrative.</p>
        <div class="atlas-list">
          {''.join(_list_item(item.get('title') or 'Story Event', item.get('summary') or '', meta=[item.get('event_type') or '', item.get('happened_at_local') or '']) for item in story_river[:5])}
        </div>
        <div class="atlas-meta"><a href="{dashboard_url('/dashboard/story-river', token)}">Open the full Story River</a></div>
      </section>
      <section class="atlas-card atlas-card--span-6">
        <h2>Library Preview</h2>
        <p>A live slice of the searchable vault underneath the atlas.</p>
        <div class="atlas-list">
          {''.join(_list_item(item.get('title') or 'Item', item.get('summary') or '', meta=[item.get('facet_type') or '', item.get('source_name') or '', item.get('happened_at_local') or '']) for item in library_preview[:5])}
        </div>
        <div class="atlas-meta"><a href="{dashboard_url('/dashboard/library', token)}">Browse the Library Explorer</a></div>
      </section>
    </div>
    """
    return render_dashboard_shell(
        title="Brain Atlas",
        token=token,
        active_page="cognitive-map",
        hero_kicker="Cognitive Map",
        hero_title="Cognitive Map",
        hero_subtitle="A graph-first view of the strongest active clusters across the brain. The map is now its own page so the library can stay the default home.",
        content_html=content_html,
        page_data_json=_serialize_json(
            {
                "facets": facets,
                "links": list(snapshot.get("links") or []),
                "current_headspace": current_headspace,
                "memory_paths": memory_paths,
            }
        ),
    )


def _render_library_page(
    items: list[dict],
    *,
    token: str,
    q: str,
    facet: str,
    record_kind: str,
) -> HTMLResponse:
    cards = "".join(
        _list_item(
            item.get("title") or "Item",
            item.get("summary") or "",
            meta=[
                item.get("record_kind") or "",
                item.get("facet") or "",
                item.get("provenance_kind") or "",
                item.get("source_type") or "",
                item.get("happened_at_local") or "",
            ],
        )
        for item in items
    ) or '<div class="atlas-empty">No library items matched these filters.</div>'
    content_html = f"""
    <section class="atlas-card atlas-card--span-12">
      <form class="atlas-form" method="get" action="/dashboard/library">
        <input type="hidden" name="token" value="{html.escape(token)}" />
        <label>Search<input type="text" name="q" value="{html.escape(q)}" placeholder="Search the vault" /></label>
        <label>Record Kind
          <select name="record_kind">
            <option value="">All records</option>
            {''.join(f'<option value="{name}" {"selected" if record_kind == name else ""}>{name.title()}</option>' for name in ('thread','episode','observation','entity','synthesis','evidence'))}
          </select>
        </label>
        <label>Facet<input type="text" name="facet" value="{html.escape(facet)}" placeholder="project, concept, decision, replay" /></label>
        <button type="submit">Filter</button>
      </form>
      <div class="atlas-list">{cards}</div>
    </section>
    """
    return render_dashboard_shell(
        title="Library Explorer",
        token=token,
        active_page="library",
        hero_kicker="Canonical Library",
        hero_title="Library Explorer",
        hero_subtitle="Browse the actual stored memory model directly: threads, episodes, observations, entities, syntheses, and thin evidence wrappers.",
        content_html=content_html,
    )


def _render_story_river_page(events: list[dict], *, token: str) -> HTMLResponse:
    timeline = "".join(
        (
            '<article class="atlas-timeline-card">'
            f"<strong>{html.escape(item.get('title') or 'Event')}</strong>"
            f"<p>{html.escape(item.get('summary') or '')}</p>"
            f"{_meta_line([item.get('event_type') or '', item.get('signal_kind') or '', item.get('happened_at_local') or ''])}"
            "</article>"
        )
        for item in events
    ) or '<div class="atlas-empty">No story events yet.</div>'
    content_html = f'<section class="atlas-card atlas-card--span-12"><div class="atlas-timeline">{timeline}</div></section>'
    return render_dashboard_shell(
        title="Story River",
        token=token,
        active_page="story-river",
        hero_kicker="Narrative Flow",
        hero_title="Story River",
        hero_subtitle="Boards, sessions, and major updates arranged as one flowing narrative instead of disconnected database rows.",
        content_html=content_html,
    )


def _render_media_page(media_facets: list[dict], *, token: str) -> HTMLResponse:
    content_html = (
        '<section class="atlas-card atlas-card--span-12"><div class="atlas-grid">'
        + "".join(
            (
                '<article class="atlas-card atlas-card--span-4">'
                f"<h2>{html.escape(item.get('title') or 'Media')}</h2>"
                f"<p>{html.escape(item.get('summary') or '')}</p>"
                f"<div class=\"atlas-list\">"
                + "".join(
                    _list_item(
                        evidence.get("title") or "Evidence",
                        evidence.get("summary") or "",
                        meta=[evidence.get("signal_kind") or "", evidence.get("happened_at_local") or ""],
                    )
                    for evidence in (item.get("evidence") or [])[:3]
                )
                + "</div></article>"
            )
            for item in media_facets
        )
        + "</div></section>"
    )
    return render_dashboard_shell(
        title="Media Signals",
        token=token,
        active_page="media",
        hero_kicker="Taste & Consumption",
        hero_title="Media Signals",
        hero_subtitle="YouTube, OTT, and other recurring media patterns distilled into themes rather than raw click clutter.",
        content_html=content_html,
    )


def _render_subconscious_page(subconscious: list[dict], *, token: str) -> HTMLResponse:
    certainty_class = {
        "grounded observation": "atlas-badge atlas-badge--grounded",
        "plausible inference": "atlas-badge atlas-badge--plausible",
        "speculative hypothesis": "atlas-badge atlas-badge--speculative",
    }
    content_html = (
        '<section class="atlas-card atlas-card--span-12"><div class="atlas-columns">'
        + "".join(
            (
                '<article class="atlas-card">'
                f"<div class=\"atlas-chip-row\"><span class=\"atlas-pill atlas-pill--warm\">{html.escape(item.get('lane') or 'Lane')}</span>"
                f"<span class=\"{certainty_class.get(item.get('certainty') or '', 'atlas-badge')}\">{html.escape(item.get('certainty') or 'unknown')}</span></div>"
                f"<h2>{html.escape(item.get('title') or 'Insight')}</h2>"
                f"<p>{html.escape(item.get('summary') or '')}</p>"
                f"<div class=\"atlas-section-title\">Why now</div><p>{html.escape(item.get('why_now') or '')}</p>"
                f"<div class=\"atlas-list\">"
                + "".join(
                    _list_item(
                        evidence.get("title") or "Evidence",
                        evidence.get("summary") or "",
                        meta=[evidence.get("signal_kind") or "", evidence.get("happened_at_local") or ""],
                    )
                    for evidence in (item.get("evidence") or [])[:2]
                )
                + "</div></article>"
            )
            for item in subconscious
        )
        + "</div></section>"
    )
    return render_dashboard_shell(
        title="Subconscious Lab",
        token=token,
        active_page="subconscious",
        hero_kicker="Quiet Churn",
        hero_title="Subconscious Lab",
        hero_subtitle="Replay, mapping, dream recombination, and foresight stay quiet here until they are useful enough to surface elsewhere.",
        content_html=content_html,
    )


def _render_health_page(snapshot: dict, *, token: str) -> HTMLResponse:
    health = dict(snapshot.get("health") or {})
    latest_syncs = list(health.get("latest_syncs") or [])
    content_html = f"""
    <div class="atlas-grid">
      <section class="atlas-card atlas-card--span-12">
        <div class="atlas-stat-row">
          {_metric('Timezone', health.get('display_timezone') or 'unknown')}
          {_metric('Generated', health.get('generated_at_local') or 'unknown')}
          {_metric('Facet Count', str(health.get('facet_count', 0)))}
          {_metric('Pending Review', str(health.get('pending_review_count', 0)))}
        </div>
      </section>
      <section class="atlas-card atlas-card--span-7">
        <h2>Sync Freshness</h2>
        <div class="atlas-list">
          {''.join(_list_item(item.get('source_name') or item.get('source_id') or 'sync', f"mode={item.get('mode')} | status={item.get('status')} | seen={item.get('items_seen')} | imported={item.get('items_imported')}", meta=[item.get('source_type') or '', item.get('started_at_local') or '']) for item in latest_syncs)}
        </div>
      </section>
      <section class="atlas-card atlas-card--span-5">
        <h2>Inspector Links</h2>
        <div class="atlas-list">
          {_list_item('Artifacts', 'Open the raw intake ledger and moderation state.', meta=[])}
          {_list_item('Boards', 'Inspect included and excluded board inputs.', meta=[])}
          {_list_item('Query Traces', 'Trace exact retrieval and narration decisions.', meta=[])}
          {_list_item('Evals', 'Check the regression harness and scoring.', meta=[])}
        </div>
        <div class="atlas-meta">
          <a href="{dashboard_url('/dashboard/artifacts', token)}">Artifacts</a>
          <a href="{dashboard_url('/dashboard/boards', token)}">Boards</a>
          <a href="{dashboard_url('/dashboard/query-traces', token)}">Query Traces</a>
          <a href="{dashboard_url('/dashboard/evals', token)}">Evals</a>
        </div>
      </section>
    </div>
    """
    return render_dashboard_shell(
        title="Brain Health",
        token=token,
        active_page="health",
        hero_kicker="Operations",
        hero_title="Brain Health",
        hero_subtitle="Freshness, ingest quality, review pressure, and retrieval reliability, all normalized to New York local time.",
        content_html=content_html,
    )


def _render_cleanup_page(payload: dict, *, token: str) -> HTMLResponse:
    samples = list(payload.get("samples") or [])
    count_cards = f"""
    <div class="atlas-stat-row">
      {_metric('Candidates', str(payload.get('candidate_count', 0)))}
      {_metric('Legacy Sources', str(payload.get('source_candidate_count', 0)))}
      {_metric('Derived Journals', str(payload.get('journal_candidate_count', 0)))}
      {_metric('Reset Links', ', '.join(payload.get('story_connections_reset') or ['none']))}
    </div>
    """
    counts_html = "".join(
        _list_item(source_type, f"{count} candidates", meta=["source family"])
        for source_type, count in sorted((payload.get("by_source_type") or {}).items())
    ) or '<div class="atlas-empty">No cleanup candidates are waiting.</div>'
    entry_type_html = "".join(
        _list_item(entry_type, f"{count} rows", meta=["entry type"])
        for entry_type, count in sorted((payload.get("by_entry_type") or {}).items(), key=lambda item: (-item[1], item[0]))
    ) or '<div class="atlas-empty">No low-signal entry types are active.</div>'
    samples_html = "".join(
        _list_item(
            item.get("title") or "Candidate",
            f"{item.get('kind')}: {item.get('entry_type')}",
            meta=[item.get("source_type") or "", item.get("project") or ""],
        )
        for item in samples
    ) or '<div class="atlas-empty">No sample candidates to inspect.</div>'
    content_html = f"""
    <div class="atlas-grid">
      <section class="atlas-card atlas-card--span-12">
        {count_cards}
      </section>
      <section class="atlas-card atlas-card--span-4">
        <h2>By Source</h2>
        <div class="atlas-list">{counts_html}</div>
      </section>
      <section class="atlas-card atlas-card--span-4">
        <h2>By Entry Type</h2>
        <div class="atlas-list">{entry_type_html}</div>
      </section>
      <section class="atlas-card atlas-card--span-4">
        <h2>Runbook</h2>
        <div class="atlas-list">
          {_list_item('1. Promote canonical memory', 'The cleanup starts by refreshing the canonical library so value is preserved before anything is deleted.', meta=[])}
          {_list_item('2. Prune legacy rows', 'Old source dumps and stale derived journal entries are deleted with their canonical echoes.', meta=[])}
          {_list_item('3. Reset story links', 'Co-signal story links are cleared so the atlas can rebuild from cleaner evidence.', meta=[])}
        </div>
      </section>
      <section class="atlas-card atlas-card--span-12">
        <h2>Sample Candidates</h2>
        <div class="atlas-list">{samples_html}</div>
      </section>
    </div>
    """
    return render_dashboard_shell(
        title="Cleanup Preview",
        token=token,
        active_page="cleanup",
        hero_kicker="Promotion Then Prune",
        hero_title="Cleanup Preview",
        hero_subtitle="This shows the exact story-era rows queued for deletion after their value has been promoted into the canonical library.",
        content_html=content_html,
    )


def _render_final_data_page(payload: dict, *, token: str) -> HTMLResponse:
    counts = dict(payload.get("counts") or {})
    items = list(payload.get("items") or [])
    content_html = f"""
    <div class="atlas-grid">
      <section class="atlas-card atlas-card--span-12">
        <div class="atlas-stat-row">
          {_metric('Threads', str(counts.get('threads', 0)))}
          {_metric('Episodes', str(counts.get('episodes', 0)))}
          {_metric('Observations', str(counts.get('observations', 0)))}
          {_metric('Entities', str(counts.get('entities', 0)))}
          {_metric('Syntheses', str(counts.get('syntheses', 0)))}
          {_metric('Evidence', str(counts.get('evidence', 0)))}
        </div>
      </section>
      <section class="atlas-card atlas-card--span-12">
        <h2>Canonical Truth</h2>
        <p>This is the final stored data the brain is treating as its current canonical memory layer.</p>
        <div class="atlas-list">
          {''.join(_list_item(item.get('title') or 'Item', item.get('summary') or '', meta=[item.get('record_kind') or '', item.get('facet') or '', item.get('provenance_kind') or '', item.get('happened_at_local') or '']) for item in items)}
        </div>
      </section>
    </div>
    """
    return render_dashboard_shell(
        title="Final Stored Data",
        token=token,
        active_page="final-data",
        hero_kicker="Canonical Truth",
        hero_title="Final Stored Data",
        hero_subtitle="A direct look at the records the brain currently treats as canonical memory, not just presentational story output.",
        content_html=content_html,
    )


def _render_brain_os_page(payload: dict, *, token: str) -> HTMLResponse:
    capabilities = list(payload.get("capabilities") or [])
    protocols = dict(payload.get("protocols") or {})
    content_html = f"""
    <div class="atlas-grid">
      <section class="atlas-card atlas-card--span-12">
        <h2>Connection Contract</h2>
        <div class="atlas-list">
          {''.join(_list_item(name.upper(), value.get('auth') or '', meta=[value.get('base_url') or value.get('transport') or value.get('bootstrap') or '']) for name, value in protocols.items())}
        </div>
      </section>
      <section class="atlas-card atlas-card--span-7">
        <h2>Capabilities</h2>
        <div class="atlas-list">
          {''.join(_list_item(item.get('title') or 'Capability', item.get('summary') or '', meta=[item.get('protocol') or '', item.get('key') or '']) for item in capabilities)}
        </div>
      </section>
      <section class="atlas-card atlas-card--span-5">
        <h2>Agent Loop</h2>
        <div class="atlas-list">
          {''.join(_list_item(step.title(), desc, meta=[]) for step, desc in (payload.get('flows') or {}).items())}
        </div>
        <div class="atlas-section-title">MCP quickstart</div>
        <div class="atlas-list">
          {''.join(_list_item(f'Step {index}', item, meta=[]) for index, item in enumerate(payload.get('mcp_quickstart') or [], 1))}
        </div>
      </section>
    </div>
    """
    return render_dashboard_shell(
        title="Brain OS",
        token=token,
        active_page="brain-os",
        hero_kicker="Agent Protocol",
        hero_title="Brain OS",
        hero_subtitle="How the brain connects, what it can do, and how external agents should bootstrap, query, publish progress, close out, and access secrets safely.",
        content_html=content_html,
    )


def _render_secret_vault_page(inventory: list[dict], *, token: str) -> HTMLResponse:
    cards = "".join(
        (
            _list_item(
                item.get("label") or "Secret",
                " | ".join(
                    value
                    for value in [
                        f"masked={item.get('masked_preview')}",
                        f"user={item.get('username') or 'n/a'}",
                        f"versions={item.get('version_count') or 1}",
                        f"aliases={', '.join(item.get('aliases') or []) or 'none'}",
                    ]
                    if value
                ),
                meta=[item.get("category") or "", item.get("owner_scope") or "", item.get("updated_at") or ""],
            )
            + "".join(
                _list_item(
                    f"Version {index}",
                    " | ".join(
                        value
                        for value in [
                            f"masked={version.get('masked_preview')}",
                            f"user={version.get('username') or 'n/a'}",
                            "current" if version.get("is_current") else "historical",
                        ]
                        if value
                    ),
                    meta=[version.get("created_at") or "", version.get("superseded_at") or ""],
                )
                for index, version in enumerate(item.get("versions") or [], start=1)
            )
        )
        for item in inventory
    ) or '<div class="atlas-empty">No secrets are in the owner vault yet.</div>'
    content_html = f"""
    <div class="atlas-grid">
      <section class="atlas-card atlas-card--span-12">
        <h2>Owner Vault</h2>
        <p>Secrets are encrypted at rest, versioned over time, and never appear in public channels, traces, boards, digests, or normal retrieval output. Owner DM is the trusted direct-reveal lane; dashboard reveal still uses OTP step-up per reveal.</p>
      </section>
      <section class="atlas-card atlas-card--span-7">
        <h2>Masked Inventory</h2>
        <div class="atlas-list">{cards}</div>
      </section>
      <section class="atlas-card atlas-card--span-5">
        <h2>Reveal Workflow</h2>
        <div class="atlas-list">
          {_list_item('Owner DM lane', 'DM KePOBot with vault commands like `vault list`, `vault show digitalocean`, or `vault history openai`. Owner DM is trusted and reveals directly there.', meta=[])}
          {_list_item('Dashboard lane', 'Use the masked vault view for browsing and history. OTP is only required if you reveal from the dashboard or API.', meta=[])}
          {_list_item('Rotation support', 'New passwords create new versions automatically. The latest version becomes current unless you explicitly pin another one.', meta=[])}
        </div>
      </section>
    </div>
    """
    return render_dashboard_shell(
        title="Secret Vault",
        token=token,
        active_page="secret-vault",
        hero_kicker="Owner Access Only",
        hero_title="Secret Vault",
        hero_subtitle="Masked inventory, version history, and the split reveal model: direct in owner DM, OTP-gated from dashboard and API.",
        content_html=content_html,
    )


def _render_public_facts_page(facts: list[dict], *, token: str) -> HTMLResponse:
    rows = "".join(
        _list_item(
            item.get("title") or "Public fact",
            item.get("body") or "",
            meta=[
                item.get("fact_type") or "",
                item.get("facet") or "",
                "approved" if item.get("approved") else "proposed",
                item.get("project_slug") or "",
            ],
        )
        for item in facts
    ) or '<div class="atlas-empty">No approved public facts exist yet.</div>'
    content_html = f"""
    <div class="atlas-grid">
      <section class="atlas-card atlas-card--span-12">
        <h2>Public Allowlist Layer</h2>
        <p>Public pages and the public chatbot read only from these approved facts and their derived snapshots. Nothing private becomes public automatically.</p>
        <div class="atlas-list">
          {_list_item('Seed from interview prep', 'POST /api/dashboard/public-facts/seed pulls from CompanyInterviewPrep markdown and converts it into approved public-safe facts.', meta=[])}
          {_list_item('Refresh snapshots', 'POST /api/dashboard/public-facts/refresh rebuilds the public profile, projects, and FAQ from approved facts only.', meta=[])}
        </div>
      </section>
      <section class="atlas-card atlas-card--span-12">
        <h2>Approved Facts</h2>
        <div class="atlas-list">{rows}</div>
      </section>
    </div>
    """
    return render_dashboard_shell(
        title="Public Facts",
        token=token,
        active_page="public-facts",
        hero_kicker="Public Surface",
        hero_title="Approved Public Facts",
        hero_subtitle="The allowlist layer that powers the public portfolio and public profile chatbot.",
        content_html=content_html,
    )


@router.get("/dashboard", dependencies=[Depends(require_dashboard_token)])
async def dashboard_root(token: str = Query(default="")) -> RedirectResponse:
    return RedirectResponse(url=dashboard_url("/dashboard/overview", token), status_code=status.HTTP_302_FOUND)


async def _profile_models() -> dict[str, dict]:
    async with async_session() as session:
        return await materialize_profile_read_models(session)


@router.get("/dashboard/overview", dependencies=[Depends(require_dashboard_token)], response_class=HTMLResponse)
async def dashboard_overview(token: str = Query(default="")) -> HTMLResponse:
    payload = (await _profile_models()).get("profile:overview", {})
    return _render_overview_page(payload, token=token)


@router.get("/dashboard/timeline", dependencies=[Depends(require_dashboard_token)], response_class=HTMLResponse)
async def dashboard_timeline(token: str = Query(default="")) -> HTMLResponse:
    payload = (await _profile_models()).get("profile:timeline", {})
    return _render_timeline_page(payload, token=token)


@router.get("/dashboard/expertise", dependencies=[Depends(require_dashboard_token)], response_class=HTMLResponse)
async def dashboard_expertise(token: str = Query(default="")) -> HTMLResponse:
    payload = (await _profile_models()).get("profile:expertise", {})
    return _render_expertise_page(payload, token=token)


@router.get("/dashboard/sources", dependencies=[Depends(require_dashboard_token)], response_class=HTMLResponse)
async def dashboard_sources(token: str = Query(default="")) -> HTMLResponse:
    payload = (await _profile_models()).get("profile:sources", {})
    return _render_sources_page(payload, token=token)


@router.get("/dashboard/coverage", dependencies=[Depends(require_dashboard_token)], response_class=HTMLResponse)
async def dashboard_coverage(token: str = Query(default="")) -> HTMLResponse:
    payload = (await _profile_models()).get("profile:coverage", {})
    return _render_coverage_page(payload, token=token)


@router.get("/dashboard/atlas", dependencies=[Depends(require_dashboard_token)])
async def dashboard_atlas_redirect(token: str = Query(default="")) -> RedirectResponse:
    return RedirectResponse(url=dashboard_url("/dashboard/cognitive-map", token), status_code=status.HTTP_302_FOUND)


@router.get("/dashboard/cognitive-map", dependencies=[Depends(require_dashboard_token)], response_class=HTMLResponse)
async def dashboard_cognitive_map(token: str = Query(default="")) -> HTMLResponse:
    async with async_session() as session:
        snapshot = (await build_brain_atlas_snapshot(session)).as_dict()
    return _render_atlas_page(snapshot, token=token)


@router.get("/dashboard/library", dependencies=[Depends(require_dashboard_token)], response_class=HTMLResponse)
async def dashboard_library(
    token: str = Query(default=""),
    q: str = Query(default=""),
    facet: str = Query(default=""),
    record_kind: str = Query(default=""),
) -> HTMLResponse:
    async with async_session() as session:
        items = await build_library_catalog(
            session,
            q=q.strip() or None,
            record_kind=record_kind or None,
            facet=facet or None,
            sync=False,
        )
    return _render_library_page(items, token=token, q=q, facet=facet, record_kind=record_kind)


@router.get("/dashboard/final-data", dependencies=[Depends(require_dashboard_token)], response_class=HTMLResponse)
async def dashboard_final_data(token: str = Query(default="")) -> HTMLResponse:
    async with async_session() as session:
        payload = await build_final_stored_data(session)
    return _render_final_data_page(payload, token=token)


@router.get("/dashboard/story-river", dependencies=[Depends(require_dashboard_token)], response_class=HTMLResponse)
async def dashboard_story_river(token: str = Query(default="")) -> HTMLResponse:
    async with async_session() as session:
        snapshot = (await build_brain_atlas_snapshot(session)).as_dict()
    return _render_story_river_page(list(snapshot.get("story_river") or []), token=token)


@router.get("/dashboard/media", dependencies=[Depends(require_dashboard_token)], response_class=HTMLResponse)
async def dashboard_media(token: str = Query(default="")) -> HTMLResponse:
    async with async_session() as session:
        snapshot = (await build_brain_atlas_snapshot(session)).as_dict()
    media_facets = [facet for facet in snapshot.get("facets", []) if facet.get("facet_type") == "media"]
    return _render_media_page(media_facets, token=token)


@router.get("/dashboard/subconscious", dependencies=[Depends(require_dashboard_token)], response_class=HTMLResponse)
async def dashboard_subconscious(token: str = Query(default="")) -> HTMLResponse:
    async with async_session() as session:
        snapshot = (await build_brain_atlas_snapshot(session, include_web=False)).as_dict()
    return _render_subconscious_page(list(snapshot.get("subconscious") or []), token=token)


@router.get("/dashboard/brain-os", dependencies=[Depends(require_dashboard_token)], response_class=HTMLResponse)
async def dashboard_brain_os(token: str = Query(default="")) -> HTMLResponse:
    async with async_session() as session:
        payload = await build_brain_self_description(session)
    return _render_brain_os_page(payload, token=token)


@router.get("/dashboard/secret-vault", dependencies=[Depends(require_dashboard_token)], response_class=HTMLResponse)
async def dashboard_secret_vault(token: str = Query(default="")) -> HTMLResponse:
    async with async_session() as session:
        inventory = await build_secret_inventory(session)
    return _render_secret_vault_page(inventory, token=token)


@router.get("/dashboard/public-facts", dependencies=[Depends(require_dashboard_token)], response_class=HTMLResponse)
async def dashboard_public_facts(token: str = Query(default="")) -> HTMLResponse:
    async with async_session() as session:
        facts = await _public_fact_payloads(session)
    return _render_public_facts_page(facts, token=token)


@router.get("/dashboard/public-surface", dependencies=[Depends(require_dashboard_token)], response_class=HTMLResponse)
async def dashboard_public_surface(token: str = Query(default="")) -> HTMLResponse:
    async with async_session() as session:
        ops = await get_public_surface_ops_status(session)
    reviews = ops.get("staged_reviews") or []
    review_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('subject_slug') or ''))}</td>"
        f"<td>{html.escape(str(item.get('status') or ''))}</td>"
        f"<td>{html.escape(str(item.get('diff_summary') or ''))}</td>"
        f"<td>{html.escape(str(item.get('created_at') or ''))}</td>"
        "</tr>"
        for item in reviews
    ) or "<tr><td colspan='4'>No staged reviews yet.</td></tr>"
    cycle = ops.get("latest_cycle") or {}
    campaign = ops.get("campaign") or {}
    report = cycle.get("report") or {}
    report_items = "".join(
        "<li>"
        f"<strong>{html.escape(str(item.get('title') or 'Update'))}</strong>: "
        f"{html.escape(str(item.get('why') or ''))}"
        "</li>"
        for item in list(report.get("improvements") or [])[:4]
    ) or "<li>No cycle report yet.</li>"
    stage_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('stage') or ''))}</td>"
        f"<td>{html.escape(str(item.get('status') or ''))}</td>"
        f"<td>{html.escape(str(item.get('summary') or ''))}</td>"
        "</tr>"
        for item in list(report.get("stages") or [])
    ) or "<tr><td colspan='3'>No stage data yet.</td></tr>"
    body = f"""
    <h1>Public Surface Ops</h1>
    <p>Morning refreshes, staged review cards, and the autonomous campaign all land here before or alongside Discord visibility. The campaign now pauses every 5 cycles for approval before the next wave can start.</p>
    <div class="atlas-panel-grid">
      <section class="atlas-panel-card">
        <h2>Latest Refresh</h2>
        <p>Status: {html.escape(str(ops.get("latest_public_run_status") or "never-run"))}</p>
        <p>Refreshed: {html.escape(str(ops.get("last_public_refresh_at") or "n/a"))}</p>
        <p>{html.escape(str(ops.get("latest_public_refresh_summary") or ""))}</p>
      </section>
      <section class="atlas-panel-card">
        <h2>Campaign</h2>
        <p>{html.escape(str(campaign.get("campaign_key") or "missing"))}</p>
        <p>Status: {html.escape(str(campaign.get("status") or ""))}</p>
        <p>Cycles: {campaign.get("completed_cycles")}/{campaign.get("target_cycles")}</p>
        <p>Latest wave: {campaign.get("latest_wave")}</p>
        <p>Approval gate: {"waiting for approval" if campaign.get("awaiting_approval") else "not blocking"}</p>
      </section>
      <section class="atlas-panel-card">
        <h2>Latest Cycle</h2>
        <p>Cycle: {html.escape(str(cycle.get("cycle_number") or ""))}</p>
        <p>Status: {html.escape(str(cycle.get("status") or ""))}</p>
        <p>{html.escape(str(cycle.get("summary") or ""))}</p>
        <p>Approval required: {html.escape(str(cycle.get("approval_required") or False))}</p>
      </section>
    </div>
    <h2>Latest Cycle Report</h2>
    <p>{html.escape(str(report.get("overview") or "No cycle report yet."))}</p>
    <ul>{report_items}</ul>
    <h2>Stage Breakdown</h2>
    <table><thead><tr><th>Stage</th><th>Status</th><th>Summary</th></tr></thead><tbody>{stage_rows}</tbody></table>
    <h2>Staged Reviews</h2>
    <table><thead><tr><th>Subject</th><th>Status</th><th>Summary</th><th>Created</th></tr></thead><tbody>{review_rows}</tbody></table>
    """
    return _page("Public Surface", body, token=token)


@router.get("/dashboard/health", dependencies=[Depends(require_dashboard_token)], response_class=HTMLResponse)
async def dashboard_health(token: str = Query(default="")) -> HTMLResponse:
    async with async_session() as session:
        snapshot = (await build_brain_atlas_snapshot(session)).as_dict()
    return _render_health_page(snapshot, token=token)


@router.get("/dashboard/cleanup", dependencies=[Depends(require_dashboard_token)], response_class=HTMLResponse)
async def dashboard_cleanup(token: str = Query(default="")) -> HTMLResponse:
    async with async_session() as session:
        payload = await build_library_cleanup_preview(session)
    return _render_cleanup_page(payload, token=token)


@router.get("/dashboard/artifacts", dependencies=[Depends(require_dashboard_token)], response_class=HTMLResponse)
async def dashboard_artifacts(
    token: str = Query(default=""),
    validation_status: str | None = Query(default=None),
) -> HTMLResponse:
    async with async_session() as session:
        items = await store.list_artifact_interpretations(session, validation_status=validation_status, limit=100)
    body = (
        "<h1>Artifact Intake</h1>"
        "<p>Latest stored captures with their current interpretation and review status.</p>"
        "<table><thead><tr><th>ID</th><th>Source</th><th>Category</th><th>Intent</th><th>Validation</th><th>Issues</th><th>Created</th></tr></thead><tbody>"
        + _render_artifact_rows(items, token)
        + "</tbody></table>"
    )
    return _page("Artifacts", body, token=token)


@router.get("/dashboard/artifacts/{artifact_id}", dependencies=[Depends(require_dashboard_token)], response_class=HTMLResponse)
async def dashboard_artifact_detail(artifact_id: uuid.UUID, token: str = Query(default="")) -> HTMLResponse:
    async with async_session() as session:
        item = await store.get_artifact_interpretation(session, artifact_id)
        if not item:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")
        reviews = [review for review in await store.get_pending_reviews(session) if review.artifact_id == artifact_id]
    artifact = item["artifact"]
    issue_lines = "".join(
        f"<li>{html.escape(issue.get('code', 'issue'))}: {html.escape(issue.get('message', ''))}</li>"
        for issue in item.get("quality_issues", [])
    ) or "<li>None</li>"
    body = f"""
    <h1>Artifact {artifact.id}</h1>
    <p><span class="pill">{html.escape(item.get('category') or 'unclassified')}</span>
    <span class="pill">{html.escape(item.get('capture_intent') or 'unknown')}</span>
    <span class="pill">{html.escape(item.get('validation_status') or 'unknown')}</span></p>
    <h2>Quality Issues</h2>
    <ul>{issue_lines}</ul>
    <h2>Pending Review Prompts</h2>
    <ul>{"".join(f"<li>{html.escape(review.question)}</li>" for review in reviews) or "<li>None</li>"}</ul>
    <h2>Raw Capture</h2>
    <pre>{html.escape((artifact.raw_text or '')[:12000])}</pre>
    """
    return _page("Artifact Detail", body, token=token)


@router.get("/dashboard/review", dependencies=[Depends(require_dashboard_token)], response_class=HTMLResponse)
async def dashboard_review(token: str = Query(default="")) -> HTMLResponse:
    async with async_session() as session:
        reviews = await store.get_pending_reviews(session)
    rows = "".join(
        "<tr>"
        f"<td>{str(review.id)[:8]}</td>"
        f"<td>{html.escape(review.review_kind)}</td>"
        f"<td>{html.escape(review.question)}</td>"
        f"<td>{_fmt_dt(review.created_at)}</td>"
        "</tr>"
        for review in reviews
    )
    body = (
        "<h1>Review Queue</h1>"
        "<table><thead><tr><th>ID</th><th>Kind</th><th>Prompt</th><th>Created</th></tr></thead><tbody>"
        + (rows or "<tr><td colspan='4'>No pending review items.</td></tr>")
        + "</tbody></table>"
    )
    return _page("Review Queue", body, token=token)


@router.get("/dashboard/notes", dependencies=[Depends(require_dashboard_token)], response_class=HTMLResponse)
async def dashboard_notes(token: str = Query(default="")) -> HTMLResponse:
    async with async_session() as session:
        notes = await store.list_notes(session, limit=200)
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(note.category)}</td>"
        f"<td>{html.escape(note.title)}</td>"
        f"<td>{html.escape((note.content or '')[:220])}</td>"
        f"<td>{_fmt_dt(note.updated_at)}</td>"
        "</tr>"
        for note in notes
    )
    body = (
        "<h1>Notes</h1>"
        "<p>Latest durable notes in the brain, across categories.</p>"
        "<table><thead><tr><th>Category</th><th>Title</th><th>Preview</th><th>Updated</th></tr></thead><tbody>"
        + (rows or "<tr><td colspan='4'>No notes yet.</td></tr>")
        + "</tbody></table>"
    )
    return _page("Notes", body, token=token)


@router.get("/dashboard/projects", dependencies=[Depends(require_dashboard_token)], response_class=HTMLResponse)
async def dashboard_projects(token: str = Query(default="")) -> HTMLResponse:
    payload = (await _profile_models()).get("profile:projects", {})
    return _render_profile_projects_page(payload, token=token)


@router.get("/dashboard/projects-legacy", dependencies=[Depends(require_dashboard_token)], response_class=HTMLResponse)
async def dashboard_projects_legacy(token: str = Query(default="")) -> HTMLResponse:
    async with async_session() as session:
        snapshot = (await build_brain_atlas_snapshot(session)).as_dict()
    project_facets = [facet for facet in snapshot.get("facets", []) if facet.get("facet_type") == "projects"]
    cards = "".join(
        (
            '<article class="atlas-card atlas-card--span-6">'
            f"<div class=\"atlas-chip-row\">{_pill(facet.get('metadata', {}).get('status') or 'unknown', warm=True)} {_pill(facet.get('happened_at_local') or 'unknown')}</div>"
            f"<h2>{html.escape(facet.get('title') or 'Project')}</h2>"
            f"<p>{html.escape(facet.get('summary') or '')}</p>"
            f"<div class=\"atlas-section-title\">Open loops</div>"
            + (
                f"<div class=\"atlas-tag-grid\">{''.join(f'<span class=\"atlas-chip\">{html.escape(value)}</span>' for value in (facet.get('open_loops') or []))}</div>"
                if facet.get("open_loops")
                else '<div class="atlas-empty">No explicit blockers or open loops.</div>'
            )
            + "<div class=\"atlas-list\">"
            + "".join(
                _list_item(
                    evidence.get("title") or "Evidence",
                    evidence.get("summary") or "",
                    meta=[evidence.get("signal_kind") or "", evidence.get("happened_at_local") or ""],
                )
                for evidence in (facet.get("evidence") or [])[:2]
            )
            + "</div></article>"
        )
        for facet in project_facets
    )
    body = (
        '<div class="atlas-grid">'
        + (cards or '<section class="atlas-card atlas-card--span-12"><div class="atlas-empty">No project facets yet.</div></section>')
        + "</div>"
    )
    return render_dashboard_shell(
        title="Projects",
        token=token,
        active_page="projects",
        hero_kicker="Active Work",
        hero_title="Project Constellation",
        hero_subtitle="Project state now reads like a field guide: where each project stands, what changed, and what still needs attention.",
        content_html=body,
    )


@router.get("/dashboard/chrome-signals", dependencies=[Depends(require_dashboard_token)], response_class=HTMLResponse)
async def dashboard_chrome_signals(token: str = Query(default="")) -> HTMLResponse:
    async with async_session() as session:
        rows = await store.list_source_items_with_sources(session, source_type="chrome_activity", limit=100)
    table_rows = "".join(
        "<tr>"
        f"<td>{html.escape((row['source_item'].payload or {}).get('entry_type') or 'unknown')}</td>"
        f"<td>{html.escape(row['source_item'].title)}</td>"
        f"<td>{html.escape(((row['project_note'].title if row['project_note'] else '') or 'none'))}</td>"
        f"<td>{html.escape(str(((row['source_item'].payload or {}).get('metadata') or {}).get('profile_email') or 'unknown'))}</td>"
        f"<td>{_fmt_dt(((row['source_item'].payload or {}).get('metadata') or {}).get('coverage_start_local'))}</td>"
        f"<td>{html.escape((row['source_item'].summary or '')[:220])}</td>"
        f"<td>{_fmt_dt(row['source_item'].happened_at or row['source_item'].created_at)}</td>"
        "</tr>"
        for row in rows
    ) or "<tr><td colspan='7'>No Chrome signals yet.</td></tr>"
    body = (
        "<h1>Chrome Signals</h1>"
        "<p>Distilled Chrome profile summaries, daily signals, and project-linked browser signals.</p>"
        "<table><thead><tr><th>Entry Type</th><th>Title</th><th>Project</th><th>Profile</th><th>Coverage Start</th><th>Summary</th><th>Created</th></tr></thead><tbody>"
        + table_rows
        + "</tbody></table>"
    )
    return _page("Chrome Signals", body, token=token)


@router.get("/dashboard/boards", dependencies=[Depends(require_dashboard_token)], response_class=HTMLResponse)
async def dashboard_boards(token: str = Query(default="")) -> HTMLResponse:
    async with async_session() as session:
        boards = await store.list_boards(session, limit=30)
    rows = "".join(
        "<tr>"
        f"<td><a href=\"{dashboard_url(f'/dashboard/boards/{board.id}', token)}\">{html.escape(board.board_type)}</a></td>"
        f"<td>{board.generated_for_date}</td>"
        f"<td>{_fmt_dt(board.coverage_start)} -> {_fmt_dt(board.coverage_end)}</td>"
        f"<td>{board.status}</td>"
        f"<td>{len(board.source_artifact_ids or [])}</td>"
        f"<td>{len(board.excluded_artifact_ids or [])}</td>"
        "</tr>"
        for board in boards
    )
    body = (
        "<h1>Boards</h1>"
        "<table><thead><tr><th>Type</th><th>For Date</th><th>Coverage</th><th>Status</th><th>Sources</th><th>Excluded</th></tr></thead><tbody>"
        + rows
        + "</tbody></table>"
    )
    return _page("Boards", body, token=token)


@router.get("/dashboard/boards/{board_id}", dependencies=[Depends(require_dashboard_token)], response_class=HTMLResponse)
async def dashboard_board_detail(board_id: uuid.UUID, token: str = Query(default="")) -> HTMLResponse:
    async with async_session() as session:
        board = await store.get_board(session, board_id)
        if not board:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Board not found")
    payload = dict(board.payload or {})
    included_rows = "".join(
        "<tr>"
        f"<td>{html.escape(item.get('title') or 'source')}</td>"
        f"<td>{html.escape(item.get('signal_kind') or 'unknown')}</td>"
        f"<td>{html.escape(item.get('reason') or '')}</td>"
        f"<td>{_fmt_dt(item.get('event_time_local'))}</td>"
        "</tr>"
        for item in payload.get("included_source_reasons", [])
    ) or "<tr><td colspan='4'>None</td></tr>"
    excluded_rows = "".join(
        "<tr>"
        f"<td>{html.escape(item.get('title') or 'source')}</td>"
        f"<td>{html.escape(item.get('signal_kind') or 'unknown')}</td>"
        f"<td>{html.escape(item.get('reason') or '')}</td>"
        f"<td>{_fmt_dt(item.get('event_time_local'))}</td>"
        "</tr>"
        for item in payload.get("excluded_source_reasons", [])
    ) or "<tr><td colspan='4'>None</td></tr>"
    body = f"""
    <h1>{html.escape(str(board.board_type).title())} Board</h1>
    <p><span class="pill">{html.escape(payload.get('coverage_label') or str(board.generated_for_date))}</span>
    <span class="pill">{html.escape(payload.get('display_timezone') or 'UTC')}</span></p>
    <h2>Story</h2>
    <p>{html.escape(payload.get('story') or 'No story text.')}</p>
    <h2>Included Inputs</h2>
    <table><thead><tr><th>Title</th><th>Signal</th><th>Reason</th><th>Local Time</th></tr></thead><tbody>{included_rows}</tbody></table>
    <h2>Excluded Inputs</h2>
    <table><thead><tr><th>Title</th><th>Signal</th><th>Reason</th><th>Local Time</th></tr></thead><tbody>{excluded_rows}</tbody></table>
    """
    return _page("Board Detail", body, token=token)


@router.get("/dashboard/query-traces", dependencies=[Depends(require_dashboard_token)], response_class=HTMLResponse)
async def dashboard_query_traces(token: str = Query(default="")) -> HTMLResponse:
    async with async_session() as session:
        traces = await store.list_retrieval_traces(session, limit=50)
    rows = "".join(
        "<tr>"
        f"<td><a href=\"{dashboard_url(f'/dashboard/query-traces/{trace.id}', token)}\">{str(trace.id)[:8]}</a></td>"
        f"<td>{html.escape((trace.question or '')[:120])}</td>"
        f"<td>{html.escape(trace.resolved_mode)}</td>"
        f"<td>{html.escape(trace.resolved_intent)}</td>"
        f"<td>{html.escape(trace.failure_stage or 'ok')}</td>"
        f"<td>{_fmt_dt(trace.created_at)}</td>"
        "</tr>"
        for trace in traces
    ) or "<tr><td colspan='6'>No query traces yet.</td></tr>"
    body = (
        "<h1>Query Traces</h1>"
        "<table><thead><tr><th>ID</th><th>Question</th><th>Mode</th><th>Intent</th><th>Failure Stage</th><th>Created</th></tr></thead><tbody>"
        + rows
        + "</tbody></table>"
    )
    return _page("Query Traces", body, token=token)


@router.get("/dashboard/query-traces/{trace_id}", dependencies=[Depends(require_dashboard_token)], response_class=HTMLResponse)
async def dashboard_query_trace_detail(trace_id: uuid.UUID, token: str = Query(default="")) -> HTMLResponse:
    async with async_session() as session:
        trace = await store.get_retrieval_trace(session, trace_id)
        if not trace:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trace not found")
    body = f"""
    <h1>Query Trace {trace.id}</h1>
    <p><span class="pill">{html.escape(trace.resolved_mode)}</span>
    <span class="pill">{html.escape(trace.resolved_intent)}</span>
    <span class="pill">{html.escape(trace.failure_stage or 'ok')}</span></p>
    <p><strong>Question:</strong> {html.escape(trace.question)}</p>
    <p><strong>Created:</strong> {html.escape(_fmt_dt(trace.created_at))}</p>
    <h2>Evidence Quality</h2>
    <pre>{html.escape(str(trace.evidence_quality or {}))}</pre>
    <h2>Trace Payload</h2>
    <pre>{html.escape(str(trace.payload or {}))}</pre>
    """
    return _page("Query Trace Detail", body, token=token)


@router.get("/dashboard/evals", dependencies=[Depends(require_dashboard_token)], response_class=HTMLResponse)
async def dashboard_evals(token: str = Query(default="")) -> HTMLResponse:
    async with async_session() as session:
        runs = await store.list_eval_runs(session, limit=25)
    rows = "".join(
        "<tr>"
        f"<td><a href=\"{dashboard_url(f'/dashboard/evals/{run.id}', token)}\">{html.escape(run.run_name)}</a></td>"
        f"<td>{html.escape(run.status)}</td>"
        f"<td>{html.escape(str(run.summary or {}))}</td>"
        f"<td>{_fmt_dt(run.created_at)}</td>"
        "</tr>"
        for run in runs
    ) or "<tr><td colspan='4'>No eval runs yet.</td></tr>"
    body = (
        "<h1>Evaluation Runs</h1>"
        "<table><thead><tr><th>Name</th><th>Status</th><th>Summary</th><th>Created</th></tr></thead><tbody>"
        + rows
        + "</tbody></table>"
    )
    return _page("Eval Runs", body, token=token)


@router.get("/dashboard/evals/{eval_run_id}", dependencies=[Depends(require_dashboard_token)], response_class=HTMLResponse)
async def dashboard_eval_detail(eval_run_id: uuid.UUID, token: str = Query(default="")) -> HTMLResponse:
    async with async_session() as session:
        results = await store.list_eval_case_results(session, eval_run_id=eval_run_id)
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(result.case_name)}</td>"
        f"<td>{html.escape(result.status)}</td>"
        f"<td>{result.score:.2f}</td>"
        f"<td>{html.escape(result.question[:140])}</td>"
        "</tr>"
        for result in results
    ) or "<tr><td colspan='4'>No case results yet.</td></tr>"
    body = (
        "<h1>Eval Case Results</h1>"
        "<table><thead><tr><th>Case</th><th>Status</th><th>Score</th><th>Question</th></tr></thead><tbody>"
        + rows
        + "</tbody></table>"
    )
    return _page("Eval Detail", body, token=token)


@router.get("/dashboard/sync-health", dependencies=[Depends(require_dashboard_token)], response_class=HTMLResponse)
async def dashboard_sync_health(token: str = Query(default="")) -> HTMLResponse:
    async with async_session() as session:
        runs = await store.list_recent_sync_runs_with_sources(session, limit=50)
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(row['sync_source'].name)}</td>"
        f"<td>{html.escape(row['run'].mode)}</td>"
        f"<td>{html.escape(row['run'].status)}</td>"
        f"<td>{row['run'].items_seen}</td>"
        f"<td>{row['run'].items_imported}</td>"
        f"<td>{_fmt_dt(row['run'].started_at)}</td>"
        "</tr>"
        for row in runs
    )
    body = (
        "<h1>Sync Health</h1>"
        "<table><thead><tr><th>Source</th><th>Mode</th><th>Status</th><th>Seen</th><th>Imported</th><th>Started</th></tr></thead><tbody>"
        + rows
        + "</tbody></table>"
    )
    return _page("Sync Health", body, token=token)


@api_router.get("/artifacts", dependencies=[Depends(require_api_token)])
async def list_dashboard_artifacts(validation_status: str | None = None) -> list[dict]:
    async with async_session() as session:
        items = await store.list_artifact_interpretations(session, validation_status=validation_status, limit=100)
    return [
        {
            "artifact_id": str(item["artifact"].id),
            "source": item["artifact"].source,
            "category": item.get("category"),
            "capture_intent": item.get("capture_intent"),
            "validation_status": item.get("validation_status"),
            "quality_issues": item.get("quality_issues", []),
            "created_at": item["artifact"].created_at.isoformat(),
            "created_at_local": _fmt_dt(item["artifact"].created_at),
        }
        for item in items
    ]


@api_router.get("/atlas", dependencies=[Depends(require_api_token)])
async def get_dashboard_atlas() -> dict:
    async with async_session() as session:
        return (await build_brain_atlas_snapshot(session)).as_dict()


@api_router.get("/overview", dependencies=[Depends(require_api_token)])
async def get_dashboard_overview() -> dict:
    async with async_session() as session:
        payloads = await materialize_profile_read_models(session)
    return payloads.get("profile:overview", {})


@api_router.get("/timeline", dependencies=[Depends(require_api_token)])
async def get_dashboard_timeline() -> dict:
    async with async_session() as session:
        payloads = await materialize_profile_read_models(session)
    return payloads.get("profile:timeline", {})


@api_router.get("/expertise", dependencies=[Depends(require_api_token)])
async def get_dashboard_expertise() -> dict:
    async with async_session() as session:
        payloads = await materialize_profile_read_models(session)
    return payloads.get("profile:expertise", {})


@api_router.get("/projects", dependencies=[Depends(require_api_token)])
async def get_dashboard_projects_model() -> dict:
    async with async_session() as session:
        payloads = await materialize_profile_read_models(session)
    return payloads.get("profile:projects", {})


@api_router.get("/sources", dependencies=[Depends(require_api_token)])
async def get_dashboard_sources() -> dict:
    async with async_session() as session:
        payloads = await materialize_profile_read_models(session)
    return payloads.get("profile:sources", {})


@api_router.get("/coverage", dependencies=[Depends(require_api_token)])
async def get_dashboard_coverage() -> dict:
    async with async_session() as session:
        payloads = await materialize_profile_read_models(session)
    return payloads.get("profile:coverage", {})


@api_router.get("/library", dependencies=[Depends(require_api_token)])
async def get_dashboard_library(
    q: str | None = None,
    facet: str | None = None,
    record_kind: str | None = None,
) -> dict:
    async with async_session() as session:
        items = await build_library_catalog(
            session,
            q=q,
            record_kind=record_kind,
            facet=facet,
            sync=False,
        )
    return {
        "display_timezone": "America/New_York",
        "count": len(items),
        "items": items,
    }


@api_router.get("/final-data", dependencies=[Depends(require_api_token)])
async def get_dashboard_final_data() -> dict:
    async with async_session() as session:
        return await build_final_stored_data(session)


@api_router.get("/story-river", dependencies=[Depends(require_api_token)])
async def get_dashboard_story_river() -> dict:
    async with async_session() as session:
        snapshot = (await build_brain_atlas_snapshot(session)).as_dict()
    return {
        "display_timezone": snapshot.get("display_timezone"),
        "events": snapshot.get("story_river", []),
    }


@api_router.get("/media", dependencies=[Depends(require_api_token)])
async def get_dashboard_media() -> dict:
    async with async_session() as session:
        snapshot = (await build_brain_atlas_snapshot(session)).as_dict()
    return {
        "display_timezone": snapshot.get("display_timezone"),
        "facets": [facet for facet in snapshot.get("facets", []) if facet.get("facet_type") == "media"],
    }


@api_router.get("/subconscious", dependencies=[Depends(require_api_token)])
async def get_dashboard_subconscious() -> dict:
    async with async_session() as session:
        snapshot = (await build_brain_atlas_snapshot(session, include_web=False)).as_dict()
    return {
        "display_timezone": snapshot.get("display_timezone"),
        "insights": snapshot.get("subconscious", []),
    }


@api_router.get("/brain-os", dependencies=[Depends(require_api_token)])
async def get_dashboard_brain_os() -> dict:
    async with async_session() as session:
        return await build_brain_self_description(session)


@api_router.get("/secret-vault", dependencies=[Depends(require_api_token)])
async def get_dashboard_secret_vault() -> dict:
    async with async_session() as session:
        inventory = await build_secret_inventory(session)
    return {
        "count": len(inventory),
        "items": inventory,
    }


@api_router.get("/public-facts", dependencies=[Depends(require_api_token)])
async def get_dashboard_public_facts() -> dict:
    async with async_session() as session:
        facts = await _public_fact_payloads(session)
    return {"count": len(facts), "items": facts}


@api_router.post("/public-facts/seed", dependencies=[Depends(require_api_token)])
async def seed_dashboard_public_facts() -> dict:
    async with async_session() as session:
        payload = await seed_public_facts_from_interview_prep(session, approve=True)
    return payload


@api_router.post("/public-facts/refresh", dependencies=[Depends(require_api_token)])
async def refresh_dashboard_public_facts() -> dict:
    async with async_session() as session:
        payload = await refresh_public_snapshots(session, force=True)
    return payload


@api_router.get("/public-surface", dependencies=[Depends(require_api_token)])
async def get_dashboard_public_surface() -> dict:
    async with async_session() as session:
        return await get_public_surface_ops_status(session)


@api_router.post("/public-surface/refresh", dependencies=[Depends(require_api_token)])
async def run_dashboard_public_surface_refresh() -> dict:
    async with async_session() as session:
        return await run_public_surface_refresh(session, trigger="dashboard", force=True)


@api_router.post("/public-surface/cycle", dependencies=[Depends(require_api_token)])
async def run_dashboard_public_surface_cycle() -> dict:
    async with async_session() as session:
        return await run_product_improvement_cycle(session, trigger="dashboard")


@api_router.post("/public-surface/cycle/approve", dependencies=[Depends(require_api_token)])
async def approve_dashboard_public_surface_cycle() -> dict:
    async with async_session() as session:
        return await approve_product_improvement_wave(session, approved_by="dashboard-api")


@api_router.post("/public-facts/{fact_id}/approve", dependencies=[Depends(require_api_token)])
async def approve_dashboard_public_fact(fact_id: uuid.UUID) -> dict:
    async with async_session() as session:
        fact = await update_public_fact(session, fact_id, approved=True)
        if not fact:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Public fact not found")
        payload = await refresh_public_snapshots(session, force=True)
    return {"status": "approved", "fact_id": str(fact.id), "refresh": payload}


@api_router.post("/public-facts/{fact_id}/revoke", dependencies=[Depends(require_api_token)])
async def revoke_dashboard_public_fact(fact_id: uuid.UUID) -> dict:
    async with async_session() as session:
        fact = await update_public_fact(session, fact_id, approved=False)
        if not fact:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Public fact not found")
        payload = await refresh_public_snapshots(session, force=True)
    return {"status": "revoked", "fact_id": str(fact.id), "refresh": payload}


@api_router.get("/cleanup-preview", dependencies=[Depends(require_api_token)])
async def get_dashboard_cleanup_preview() -> dict:
    async with async_session() as session:
        return await build_library_cleanup_preview(session)


@api_router.get("/health", dependencies=[Depends(require_api_token)])
async def get_dashboard_health() -> dict:
    async with async_session() as session:
        snapshot = (await build_brain_atlas_snapshot(session)).as_dict()
    return snapshot.get("health", {})


@api_router.get("/artifacts/{artifact_id}", dependencies=[Depends(require_api_token)])
async def get_dashboard_artifact(artifact_id: uuid.UUID) -> dict:
    async with async_session() as session:
        item = await store.get_artifact_interpretation(session, artifact_id)
        if not item:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")
    artifact = item["artifact"]
    return {
        "artifact_id": str(artifact.id),
        "source": artifact.source,
        "raw_text": artifact.raw_text,
        "category": item.get("category"),
        "capture_intent": item.get("capture_intent"),
        "validation_status": item.get("validation_status"),
        "quality_issues": item.get("quality_issues", []),
        "created_at_local": _fmt_dt(artifact.created_at),
    }


@api_router.post("/artifacts/{artifact_id}/moderate", dependencies=[Depends(require_api_token)])
async def moderate_artifact(artifact_id: uuid.UUID, payload: ArtifactModerationRequest) -> dict:
    async with async_session() as session:
        latest = await store.get_latest_classification(session, artifact_id)
        if not latest:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Classification not found")
        values = {}
        if payload.category:
            values["category"] = normalize_category(payload.category, default=latest.category)
        if payload.capture_intent:
            values["capture_intent"] = normalize_capture_intent(payload.capture_intent, default=latest.capture_intent)
        if payload.validation_status:
            values["validation_status"] = normalize_validation_status(
                payload.validation_status,
                default=latest.validation_status,
            )
        if payload.quality_issues:
            values["quality_issues"] = payload.quality_issues
        if payload.eligible_for_boards is not None:
            values["eligible_for_boards"] = payload.eligible_for_boards
        if payload.eligible_for_project_state is not None:
            values["eligible_for_project_state"] = payload.eligible_for_project_state
        if values.get("validation_status", latest.validation_status) == "validated":
            values["is_final"] = True
        classification = await store.update_classification(session, latest.id, **values)
        for review in [review for review in await store.get_pending_reviews(session) if review.artifact_id == artifact_id]:
            await store.moderate_review(
                session,
                review.id,
                status="resolved",
                resolution=payload.moderation_notes or "Moderated from dashboard API.",
                moderation_notes=payload.moderation_notes,
                resolved_by=payload.resolved_by or "dashboard",
            )
        if classification and classification.is_final:
            pool = await get_pool()
            await pool.enqueue_job(JOB_GENERATE_EMBEDDINGS, artifact_id=str(artifact_id))
            await pool.enqueue_job(
                JOB_PROCESS_LIBRARIAN,
                artifact_id=str(artifact_id),
                classification_id=str(classification.id),
            )
        await recompute_project_states(session)
    return {
        "status": "ok",
        "artifact_id": str(artifact_id),
        "classification_id": str(classification.id) if classification else None,
    }


@api_router.get("/reviews", dependencies=[Depends(require_api_token)])
async def list_dashboard_reviews() -> list[dict]:
    async with async_session() as session:
        reviews = await store.get_pending_reviews(session)
    return [
        {
            "review_id": str(review.id),
            "artifact_id": str(review.artifact_id),
            "review_kind": review.review_kind,
            "question": review.question,
            "created_at": review.created_at.isoformat(),
            "created_at_local": _fmt_dt(review.created_at),
        }
        for review in reviews
    ]


@api_router.get("/boards", dependencies=[Depends(require_api_token)])
async def list_dashboard_boards(board_type: str | None = None) -> list[dict]:
    async with async_session() as session:
        boards = await store.list_boards(session, board_type=board_type, limit=50)
    return [
        {
            "board_id": str(board.id),
            "board_type": board.board_type,
            "generated_for_date": board.generated_for_date.isoformat(),
            "coverage_start": board.coverage_start.isoformat(),
            "coverage_end": board.coverage_end.isoformat(),
            "coverage_start_local": _fmt_dt(board.coverage_start),
            "coverage_end_local": _fmt_dt(board.coverage_end),
            "status": board.status,
            "source_artifact_ids": board.source_artifact_ids or [],
            "excluded_artifact_ids": board.excluded_artifact_ids or [],
        }
        for board in boards
    ]


@api_router.get("/chrome-signals", dependencies=[Depends(require_api_token)])
async def list_dashboard_chrome_signals() -> list[dict]:
    async with async_session() as session:
        rows = await store.list_source_items_with_sources(session, source_type="chrome_activity", limit=100)
    return [
        {
            "source_item_id": str(row["source_item"].id),
            "entry_type": (row["source_item"].payload or {}).get("entry_type"),
            "title": row["source_item"].title,
            "summary": row["source_item"].summary,
            "project": row["project_note"].title if row["project_note"] else None,
            "profile_email": ((row["source_item"].payload or {}).get("metadata") or {}).get("profile_email"),
            "coverage_start_local": _fmt_dt(((row["source_item"].payload or {}).get("metadata") or {}).get("coverage_start_local")),
            "coverage_end_local": _fmt_dt(((row["source_item"].payload or {}).get("metadata") or {}).get("coverage_end_local")),
            "happened_at_local": _fmt_dt(row["source_item"].happened_at or row["source_item"].created_at),
        }
        for row in rows
    ]


@api_router.get("/project-aliases", dependencies=[Depends(require_api_token)])
async def list_dashboard_project_aliases() -> list[dict]:
    async with async_session() as session:
        projects = await store.list_project_notes(session, limit=200)
        aliases = await store.list_project_aliases(session, limit=500)
    titles_by_id = {str(project.id): project.title for project in projects}
    grouped: dict[str, set[str]] = {project.title: {project.title} for project in projects}
    for alias in aliases:
        project_title = titles_by_id.get(str(alias.project_note_id))
        if not project_title:
            continue
        grouped.setdefault(project_title, {project_title}).add(alias.alias)
    return [
        {"project_title": project_title, "aliases": sorted(values)}
        for project_title, values in sorted(grouped.items())
    ]


@api_router.get("/boards/{board_id}", dependencies=[Depends(require_api_token)])
async def get_dashboard_board(board_id: uuid.UUID) -> dict:
    async with async_session() as session:
        board = await store.get_board(session, board_id)
        if not board:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Board not found")
    return {
        "board_id": str(board.id),
        "board_type": board.board_type,
        "generated_for_date": board.generated_for_date.isoformat(),
        "coverage_start": board.coverage_start.isoformat(),
        "coverage_end": board.coverage_end.isoformat(),
        "coverage_start_local": _fmt_dt(board.coverage_start),
        "coverage_end_local": _fmt_dt(board.coverage_end),
        "payload": board.payload,
    }


@api_router.post("/boards/regenerate", dependencies=[Depends(require_api_token)])
async def regenerate_board_route(payload: BoardRegenerateRequest) -> dict:
    target = date.fromisoformat(payload.target_date)
    async with async_session() as session:
        if payload.board_type == "daily":
            board_payload = await generate_or_refresh_board(session, window=daily_board_window(target))
        elif payload.board_type == "weekly":
            board_payload = await generate_or_refresh_board(session, window=weekly_board_window(target))
        else:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported board type")
        await recompute_project_states(session)
        if payload.board_type == "daily":
            digest_payload = await generate_or_refresh_digest(session, digest_date=target + timedelta(days=1))
        else:
            digest_payload = None
    return {
        "status": "ok",
        "board": board_payload,
        "digest": digest_payload,
    }


@api_router.get("/query-traces", dependencies=[Depends(require_api_token)])
async def list_query_traces() -> list[dict]:
    async with async_session() as session:
        traces = await store.list_retrieval_traces(session, limit=50)
    return [
        {
            "trace_id": str(trace.id),
            "question": trace.question,
            "resolved_mode": trace.resolved_mode,
            "resolved_intent": trace.resolved_intent,
            "failure_stage": trace.failure_stage,
            "evidence_quality": trace.evidence_quality or {},
            "used_exact_match": trace.used_exact_match,
            "used_project_snapshot": trace.used_project_snapshot,
            "used_vector_search": trace.used_vector_search,
            "used_web": trace.used_web,
            "created_at": trace.created_at.isoformat(),
            "created_at_local": _fmt_dt(trace.created_at),
        }
        for trace in traces
    ]


@api_router.get("/query-traces/{trace_id}", dependencies=[Depends(require_api_token)])
async def get_query_trace(trace_id: uuid.UUID) -> dict:
    async with async_session() as session:
        trace = await store.get_retrieval_trace(session, trace_id)
        if not trace:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trace not found")
    return {
        "trace_id": str(trace.id),
        "question": trace.question,
        "resolved_mode": trace.resolved_mode,
        "resolved_intent": trace.resolved_intent,
        "failure_stage": trace.failure_stage,
        "evidence_quality": trace.evidence_quality or {},
        "used_exact_match": trace.used_exact_match,
        "used_project_snapshot": trace.used_project_snapshot,
        "used_vector_search": trace.used_vector_search,
        "used_web": trace.used_web,
        "payload": trace.payload or {},
        "created_at": trace.created_at.isoformat(),
        "created_at_local": _fmt_dt(trace.created_at),
    }


@api_router.get("/eval-runs", dependencies=[Depends(require_api_token)])
async def list_eval_runs_route() -> list[dict]:
    async with async_session() as session:
        runs = await store.list_eval_runs(session, limit=25)
    return [
        {
            "eval_run_id": str(run.id),
            "run_name": run.run_name,
            "status": run.status,
            "summary": run.summary or {},
            "created_at": run.created_at.isoformat(),
            "created_at_local": _fmt_dt(run.created_at),
        }
        for run in runs
    ]


@api_router.get("/eval-runs/{eval_run_id}", dependencies=[Depends(require_api_token)])
async def get_eval_run_route(eval_run_id: uuid.UUID) -> dict:
    async with async_session() as session:
        results = await store.list_eval_case_results(session, eval_run_id=eval_run_id)
    return {
        "eval_run_id": str(eval_run_id),
        "results": [
            {
                "case_name": result.case_name,
                "status": result.status,
                "score": result.score,
                "question": result.question,
                "expected": result.expected or {},
                "actual": result.actual or {},
                "notes": result.notes,
            }
            for result in results
        ],
    }


@api_router.post("/eval-runs/run", dependencies=[Depends(require_api_token)])
async def run_eval_route(payload: EvalRunRequest) -> dict:
    async with async_session() as session:
        return await run_query_eval(session, run_name=payload.run_name, rounds=payload.rounds)


@api_router.get("/sync-health", dependencies=[Depends(require_api_token)])
async def sync_health_route() -> list[dict]:
    async with async_session() as session:
        runs = await store.list_recent_sync_runs_with_sources(session, limit=50)
    return [
        {
            "sync_run_id": str(row["run"].id),
            "sync_source_id": str(row["run"].sync_source_id),
            "sync_source_name": row["sync_source"].name,
            "sync_source_type": row["sync_source"].source_type,
            "mode": row["run"].mode,
            "status": row["run"].status,
            "items_seen": row["run"].items_seen,
            "items_imported": row["run"].items_imported,
            "started_at": row["run"].started_at.isoformat(),
            "finished_at": row["run"].finished_at.isoformat() if row["run"].finished_at else None,
            "started_at_local": _fmt_dt(row["run"].started_at),
            "finished_at_local": _fmt_dt(row["run"].finished_at) if row["run"].finished_at else None,
        }
        for row in runs
    ]
