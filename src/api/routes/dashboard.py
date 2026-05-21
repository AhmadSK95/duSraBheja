"""Lean Atlas dashboard — 5 read-only pages, no on-render LLM calls, no
recomputation. Login is unchanged."""

from __future__ import annotations

import html
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import or_, select

from src.api.dashboard_ui import dashboard_url, render_dashboard_shell
from src.database import async_session
from src.lib.auth import (
    dashboard_credentials_match,
    dashboard_username,
    require_dashboard_token,
)
from src.lib.time import format_display_datetime
from src.models import (
    Artifact,
    Classification,
    DashboardViewState,
    Note,
    ProjectStateSnapshot,
    PublicFactRecord,
)

router = APIRouter(tags=["dashboard"])

# ---------------------------------------------------------------------------
# Login / Logout
# ---------------------------------------------------------------------------


def _login_page(*, next_path: str, error: str | None = None) -> HTMLResponse:
    safe_next = html.escape(next_path or "/dashboard/")
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
        <p>Private access only.</p>
        {error_html}
        <form method="post" action="/dashboard/login" class="atlas-login-form">
          <input type="hidden" name="next" value="{safe_next}" />
          <label><span>Username</span>
            <input type="text" name="username" autocomplete="username" value="{html.escape(dashboard_username())}" required />
          </label>
          <label><span>Password</span>
            <input type="password" name="password" autocomplete="current-password" required />
          </label>
          <button type="submit">Open Atlas</button>
        </form>
      </section>
    </main>
  </body>
</html>"""
    )


@router.get("/dashboard/login", response_class=HTMLResponse)
async def dashboard_login(next: str = Query(default="/dashboard/")) -> HTMLResponse:
    return _login_page(next_path=next)


@router.post("/dashboard/login", response_class=HTMLResponse)
async def dashboard_login_submit(
    request: Request,
    username: str = Form(default=""),
    password: str = Form(default=""),
    next: str = Form(default="/dashboard/"),
) -> Response:
    if not dashboard_credentials_match(username=username, password=password):
        return _login_page(next_path=next, error="Login didn't match.")
    request.session["dashboard_authenticated"] = True
    request.session["dashboard_username"] = dashboard_username()
    destination = next if next.startswith("/dashboard") else "/dashboard/"
    return RedirectResponse(url=destination, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/dashboard/logout")
async def dashboard_logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse(url="/dashboard/login", status_code=status.HTTP_303_SEE_OTHER)


# ---------------------------------------------------------------------------
# Tiny render helpers — pure HTML strings, no JS, no LLM
# ---------------------------------------------------------------------------


def _fmt_dt(value) -> str:
    return format_display_datetime(value) if value else "—"


def _esc(value: str | None) -> str:
    return html.escape(value or "")


def _pill(text: str) -> str:
    return f'<span class="atlas-pill">{html.escape(text)}</span>'


def _section(title: str, body_html: str, kicker: str = "") -> str:
    kicker_html = f'<div class="atlas-kicker">{html.escape(kicker)}</div>' if kicker else ""
    return (
        '<section class="atlas-card">'
        f'{kicker_html}<h2>{html.escape(title)}</h2>{body_html}'
        "</section>"
    )


def _empty_state(message: str) -> str:
    return f'<p class="atlas-empty">{html.escape(message)}</p>'


def _table(rows: list[list[str]], headers: list[str]) -> str:
    head = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows
    )
    return f'<table class="atlas-table"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


# ---------------------------------------------------------------------------
# DashboardViewState — track "last seen" per user
# ---------------------------------------------------------------------------


async def _get_or_create_view_state(session, username: str) -> DashboardViewState:
    result = await session.execute(
        select(DashboardViewState).where(DashboardViewState.username == username)
    )
    state = result.scalar_one_or_none()
    if state is None:
        state = DashboardViewState(
            username=username,
            last_seen_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(state)
        await session.flush()
    return state


async def _advance_last_seen(session, state: DashboardViewState) -> None:
    state.last_seen_at = datetime.now(timezone.utc)
    state.updated_at = datetime.now(timezone.utc)
    await session.flush()


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@router.get("/dashboard", dependencies=[Depends(require_dashboard_token)])
async def dashboard_root_redirect(token: str = Query(default="")) -> RedirectResponse:
    return RedirectResponse(url=dashboard_url("/dashboard/", token), status_code=302)


@router.get("/dashboard/", dependencies=[Depends(require_dashboard_token)], response_class=HTMLResponse)
async def dashboard_home(request: Request, token: str = Query(default="")) -> HTMLResponse:
    """What's New — items created since `last_seen_at`. After render, advance it."""
    username = request.session.get("dashboard_username") or dashboard_username()
    async with async_session() as session:
        state = await _get_or_create_view_state(session, username)
        cutoff = state.last_seen_at

        new_artifacts = (
            (
                await session.execute(
                    select(Artifact)
                    .where(Artifact.created_at > cutoff)
                    .order_by(Artifact.created_at.desc())
                    .limit(50)
                )
            )
            .scalars()
            .all()
        )
        new_notes = (
            (
                await session.execute(
                    select(Note)
                    .where(Note.created_at > cutoff)
                    .order_by(Note.created_at.desc())
                    .limit(50)
                )
            )
            .scalars()
            .all()
        )

        await _advance_last_seen(session, state)
        await session.commit()

    if not new_artifacts and not new_notes:
        body = _section(
            "What's new",
            _empty_state(f"Nothing new since {_fmt_dt(cutoff)}."),
            kicker="quiet",
        )
    else:
        sections: list[str] = []
        if new_artifacts:
            rows = [
                [
                    _esc((a.summary or a.raw_text or "")[:120]),
                    _esc(a.source),
                    _esc(_fmt_dt(a.created_at)),
                ]
                for a in new_artifacts
            ]
            sections.append(
                _section(
                    f"New artifacts ({len(new_artifacts)})",
                    _table(rows, ["preview", "source", "captured"]),
                )
            )
        if new_notes:
            rows = [
                [
                    _esc(n.title or "Untitled"),
                    _pill(n.category or "note"),
                    _esc(_fmt_dt(n.created_at)),
                ]
                for n in new_notes
            ]
            sections.append(
                _section(
                    f"New notes ({len(new_notes)})",
                    _table(rows, ["title", "category", "created"]),
                )
            )
        body = "".join(sections)

    return render_dashboard_shell(
        title="Atlas — What's New",
        token=token,
        active_page="home",
        hero_kicker="atlas",
        hero_title="What's new",
        hero_subtitle=f"Since {_fmt_dt(cutoff)}",
        content_html=body,
    )


@router.get("/dashboard/inbox", dependencies=[Depends(require_dashboard_token)], response_class=HTMLResponse)
async def dashboard_inbox(token: str = Query(default="")) -> HTMLResponse:
    async with async_session() as session:
        result = await session.execute(
            select(Artifact, Classification)
            .outerjoin(Classification, Classification.artifact_id == Artifact.id)
            .order_by(Artifact.created_at.desc())
            .limit(100)
        )
        rows_data: list[tuple[Artifact, Classification | None]] = list(result.all())

    if not rows_data:
        body = _section("Inbox", _empty_state("No artifacts yet."))
    else:
        rows = [
            [
                _esc((art.summary or art.raw_text or "")[:140]),
                _esc(art.content_type or "—"),
                _pill(cls.category) if cls else _pill("unclassified"),
                _esc(f"{cls.confidence:.2f}") if cls and cls.confidence is not None else "—",
                _esc(_fmt_dt(art.created_at)),
            ]
            for art, cls in rows_data
        ]
        body = _section(
            f"Recent artifacts ({len(rows)})",
            _table(rows, ["preview", "type", "category", "confidence", "captured"]),
        )

    return render_dashboard_shell(
        title="Atlas — Inbox",
        token=token,
        active_page="inbox",
        hero_kicker="inbox",
        hero_title="Recent captures",
        hero_subtitle="Last 100 artifacts dropped into the brain.",
        content_html=body,
    )


@router.get("/dashboard/library", dependencies=[Depends(require_dashboard_token)], response_class=HTMLResponse)
async def dashboard_library(token: str = Query(default=""), q: str = Query(default="")) -> HTMLResponse:
    q = (q or "").strip()
    async with async_session() as session:
        if q:
            pattern = f"%{q}%"
            stmt = (
                select(Note)
                .where(or_(Note.title.ilike(pattern), Note.content.ilike(pattern)))
                .order_by(Note.updated_at.desc())
                .limit(50)
            )
        else:
            stmt = select(Note).order_by(Note.updated_at.desc()).limit(50)
        notes = (await session.execute(stmt)).scalars().all()

    safe_q = html.escape(q)
    search_form = (
        '<form class="atlas-search" method="get" action="/dashboard/library">'
        f'<input type="hidden" name="token" value="{html.escape(token)}" />'
        f'<input type="search" name="q" value="{safe_q}" placeholder="Search notes…" autofocus />'
        '<button type="submit">Search</button>'
        "</form>"
    )
    if not notes:
        body_inner = _empty_state("No matches." if q else "Library is empty.")
    else:
        rows = [
            [
                _esc(n.title or "Untitled"),
                _pill(n.category or "note"),
                _esc((n.content or "")[:160]),
                _esc(_fmt_dt(n.updated_at)),
            ]
            for n in notes
        ]
        body_inner = _table(rows, ["title", "category", "preview", "updated"])

    body = _section(
        "Library" + (f" — {q}" if q else ""),
        search_form + body_inner,
    )

    return render_dashboard_shell(
        title="Atlas — Library",
        token=token,
        active_page="library",
        hero_kicker="library",
        hero_title="Canonical notes",
        hero_subtitle="Full-text search across notes.",
        content_html=body,
    )


@router.get("/dashboard/projects", dependencies=[Depends(require_dashboard_token)], response_class=HTMLResponse)
async def dashboard_projects(token: str = Query(default="")) -> HTMLResponse:
    async with async_session() as session:
        result = await session.execute(
            select(ProjectStateSnapshot, Note.title)
            .join(Note, Note.id == ProjectStateSnapshot.project_note_id)
            .order_by(ProjectStateSnapshot.last_signal_at.desc().nullslast())
            .limit(50)
        )
        rows_data: list[tuple[ProjectStateSnapshot, str | None]] = list(result.all())

    if not rows_data:
        body = _section("Projects", _empty_state("No project snapshots yet."))
    else:
        rows = [
            [
                _esc(title or "Untitled"),
                _pill(snap.status or "uncertain"),
                _esc((snap.what_changed or snap.implemented or "")[:200]),
                _esc(_fmt_dt(snap.last_signal_at)),
            ]
            for snap, title in rows_data
        ]
        body = _section(
            f"Projects ({len(rows)})",
            _table(rows, ["title", "status", "last update", "last signal"]),
        )

    return render_dashboard_shell(
        title="Atlas — Projects",
        token=token,
        active_page="projects",
        hero_kicker="projects",
        hero_title="Project state",
        hero_subtitle="Read directly from the latest precomputed snapshots.",
        content_html=body,
    )


@router.get("/dashboard/public-facts", dependencies=[Depends(require_dashboard_token)], response_class=HTMLResponse)
async def dashboard_public_facts(token: str = Query(default="")) -> HTMLResponse:
    async with async_session() as session:
        pending = (
            (
                await session.execute(
                    select(PublicFactRecord)
                    .where(PublicFactRecord.approved == False)  # noqa: E712
                    .order_by(PublicFactRecord.updated_at.desc())
                    .limit(50)
                )
            )
            .scalars()
            .all()
        )
        approved_count = (
            await session.execute(
                select(PublicFactRecord).where(PublicFactRecord.approved == True)  # noqa: E712
            )
        ).scalars().all()

    if not pending:
        body = _section(
            "Public facts",
            _empty_state(
                f"Nothing pending. {len(approved_count)} facts live on the public site."
            ),
        )
    else:
        cards: list[str] = []
        for fact in pending:
            action_url = dashboard_url(
                f"/dashboard/public-facts/{fact.id}/decide", token
            )
            cards.append(
                '<article class="atlas-fact-card">'
                f'<header><h3>{_esc(fact.title or fact.fact_key)}</h3>'
                f'{_pill(fact.facet or "general")}</header>'
                f'<p>{_esc((fact.body or "")[:600])}</p>'
                f'<footer><form method="post" action="{action_url}" style="display:inline">'
                '<button name="decision" value="approve" class="atlas-btn atlas-btn--ok">Approve</button>'
                '</form> '
                f'<form method="post" action="{action_url}" style="display:inline">'
                '<button name="decision" value="reject" class="atlas-btn atlas-btn--warn">Reject</button>'
                "</form></footer></article>"
            )
        body = _section(
            f"Pending public facts ({len(pending)})",
            "".join(cards),
            kicker="human-in-the-loop",
        )

    return render_dashboard_shell(
        title="Atlas — Public facts",
        token=token,
        active_page="public-facts",
        hero_kicker="public",
        hero_title="Approval queue",
        hero_subtitle="Only approved facts reach the public chatbot.",
        content_html=body,
    )


@router.post(
    "/dashboard/public-facts/{fact_id}/decide",
    dependencies=[Depends(require_dashboard_token)],
)
async def dashboard_public_facts_decide(
    fact_id: str,
    decision: str = Form(...),
    token: str = Query(default=""),
) -> RedirectResponse:
    try:
        fid = uuid.UUID(fact_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid fact_id") from exc

    async with async_session() as session:
        fact = await session.get(PublicFactRecord, fid)
        if fact is None:
            raise HTTPException(status_code=404, detail="fact not found")
        if decision == "approve":
            fact.approved = True
            fact.updated_at = datetime.now(timezone.utc)
        elif decision == "reject":
            await session.delete(fact)
        else:
            raise HTTPException(status_code=400, detail="decision must be approve|reject")
        await session.commit()

    return RedirectResponse(
        url=dashboard_url("/dashboard/public-facts", token),
        status_code=status.HTTP_303_SEE_OTHER,
    )
