"""Commands Cog — /ask, /remember, /task, /review slash commands."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from src.constants import BRAIN_CATEGORIES
from src.bot.cogs.inbox import build_answer_embed, build_digest_embeds
from src.database import async_session
from src.lib.store import get_pending_reviews
from src.services.digest import generate_or_refresh_digest
from src.services.identity import resolve_project
from src.services.session_bootstrap import build_session_bootstrap
from src.services.reminders import store_reminder
from src.services.query import query_brain
from src.services.project_state import recompute_project_states
from src.services.story import build_project_story_payload
from src.lib import store

log = logging.getLogger("brain-bot.commands")


async def _resolve_project_payload(session, subject: str, *, session_id: str) -> tuple[object, dict, dict] | tuple[None, None, None]:
    project = await resolve_project(
        session,
        project_hint=subject,
        source_refs=[subject],
        create_if_missing=False,
    )
    if not project:
        return None, None, None
    await recompute_project_states(session, project_note_ids=[project.id])
    payload = await build_project_story_payload(session, project.id)
    bootstrap = await build_session_bootstrap(
        session,
        agent_kind="discord",
        session_id=session_id,
        project_hint=project.title,
        include_web=False,
    )
    return project, payload, bootstrap


class CommandsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="ask", description="Ask your brain a question")
    @app_commands.describe(
        question="What do you want to know?",
        category="Filter to a specific category",
        deep="Use Opus 4.6 for deeper reasoning (slower, more expensive)",
    )
    @app_commands.choices(
        category=[
            app_commands.Choice(name=cat.replace("_", " "), value=cat)
            for cat in BRAIN_CATEGORIES
        ]
    )
    async def ask(
        self,
        interaction: discord.Interaction,
        question: str,
        category: str | None = None,
        deep: bool = False,
    ):
        await interaction.response.defer(thinking=True)

        async with async_session() as session:
            result = await query_brain(
                session,
                question=question,
                mode="answer",
                category=category,
                use_opus=deep,
            )

        embed = build_answer_embed(question, result)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="latest", description="Show the latest grounded story on a subject")
    async def latest(self, interaction: discord.Interaction, subject: str, deep: bool = False):
        await interaction.response.defer(thinking=True)
        question = f"What is the latest on {subject}?"
        async with async_session() as session:
            result = await query_brain(session, question=question, mode="latest", use_opus=deep)
        await interaction.followup.send(embed=build_answer_embed(question, result))

    @app_commands.command(name="timeline", description="Show the timeline for a subject")
    async def timeline(self, interaction: discord.Interaction, subject: str, deep: bool = False):
        await interaction.response.defer(thinking=True)
        question = f"Show me the timeline for {subject}"
        async with async_session() as session:
            result = await query_brain(session, question=question, mode="timeline", use_opus=deep)
        await interaction.followup.send(embed=build_answer_embed(question, result))

    @app_commands.command(name="changed", description="Show what changed since a boundary")
    async def changed(
        self,
        interaction: discord.Interaction,
        subject: str,
        since: str = "yesterday",
        deep: bool = False,
    ):
        await interaction.response.defer(thinking=True)
        question = f"What changed since {since} on {subject}"
        async with async_session() as session:
            result = await query_brain(session, question=question, mode="changed_since", use_opus=deep)
        await interaction.followup.send(embed=build_answer_embed(question, result))

    @app_commands.command(name="sources", description="Show raw sources for a subject")
    async def sources(self, interaction: discord.Interaction, subject: str):
        await interaction.response.defer(thinking=True)
        question = f"Show sources for {subject}"
        async with async_session() as session:
            result = await query_brain(session, question=question, mode="sources")
        await interaction.followup.send(embed=build_answer_embed(question, result))

    @app_commands.command(name="project", description="Show the current project state snapshot")
    async def project(self, interaction: discord.Interaction, subject: str):
        await interaction.response.defer(thinking=True)
        async with async_session() as session:
            _project, payload, bootstrap = await _resolve_project_payload(
                session,
                subject,
                session_id=f"discord:project:{interaction.id}",
            )
            if not payload:
                await interaction.followup.send(f"Project not found: {subject}")
                return

        snapshot = payload.get("snapshot") or {}
        reboot = bootstrap.get("reboot_brief") or {}
        embed = discord.Embed(
            title=payload["project"]["title"],
            description=reboot.get("where_it_stands") or snapshot.get("implemented") or payload["project"]["content"] or "No project summary yet.",
            color=discord.Color.blue(),
        )
        embed.add_field(name="Status", value=(snapshot.get("status") or payload["project"]["status"]).title(), inline=True)
        embed.add_field(name="Active Score", value=f"{snapshot.get('active_score', 0):.2f}", inline=True)
        embed.add_field(name="Confidence", value=f"{snapshot.get('confidence', 0):.0%}", inline=True)
        if reboot.get("what_changed"):
            embed.add_field(name="What Changed", value=str(reboot["what_changed"])[:1024], inline=False)
        if reboot.get("what_is_left"):
            embed.add_field(name="What's Left", value=str(reboot["what_is_left"])[:1024], inline=False)
        if reboot.get("blockers"):
            embed.add_field(name="Blockers", value="\n".join(f"- {item}" for item in reboot["blockers"][:5]), inline=False)
        elif snapshot.get("holes"):
            embed.add_field(name="Holes", value="\n".join(f"- {item}" for item in snapshot["holes"][:5]), inline=False)
        if reboot.get("open_loops"):
            embed.add_field(name="Open Loops", value="\n".join(f"- {item}" for item in reboot["open_loops"][:5]), inline=False)
        if payload.get("connections"):
            embed.add_field(
                name="Connections",
                value="\n".join(
                    f"- {(item['target_ref'] if item['source_ref'] == payload['project']['title'] else item['source_ref'])}"
                    for item in payload["connections"][:5]
                ),
                inline=False,
            )
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="review-project", description="Critique the current approach for a project")
    async def review_project(self, interaction: discord.Interaction, subject: str, deep: bool = True):
        await interaction.response.defer(thinking=True)
        question = f"Review project {subject}. What is implemented, what is left, is this the best approach, and what holes or misses exist?"
        async with async_session() as session:
            result = await query_brain(session, question=question, mode="project_review", use_opus=deep)
        await interaction.followup.send(embed=build_answer_embed(question, result))

    @app_commands.command(name="pin-project", description="Pin a project so the brain treats it as active")
    async def pin_project(self, interaction: discord.Interaction, subject: str):
        await interaction.response.defer(thinking=True)
        async with async_session() as session:
            project = await resolve_project(session, project_hint=subject, source_refs=[subject], create_if_missing=False)
            if not project:
                await interaction.followup.send(f"Project not found: {subject}")
                return
            await store.set_project_manual_state(session, project_note_id=project.id, manual_state="pinned")
            await recompute_project_states(session, project_note_ids=[project.id])
        await interaction.followup.send(f"Pinned project: {project.title}")

    @app_commands.command(name="ignore-project", description="Mark a project as ignored or dormant")
    async def ignore_project(self, interaction: discord.Interaction, subject: str):
        await interaction.response.defer(thinking=True)
        async with async_session() as session:
            project = await resolve_project(session, project_hint=subject, source_refs=[subject], create_if_missing=False)
            if not project:
                await interaction.followup.send(f"Project not found: {subject}")
                return
            await store.set_project_manual_state(session, project_note_id=project.id, manual_state="ignored")
            await recompute_project_states(session, project_note_ids=[project.id])
        await interaction.followup.send(f"Ignoring project for active-focus ranking: {project.title}")

    @app_commands.command(name="remind", description="Create a recurring or one-time reminder")
    async def remind(self, interaction: discord.Interaction, text: str, project_name: str | None = None):
        await interaction.response.defer(thinking=True)
        async with async_session() as session:
            note = await store.create_note(
                session,
                category="reminder",
                title=text[:120],
                content=text,
                priority="medium",
                discord_channel_id=str(interaction.channel_id),
            )
            project_note_id = None
            if project_name:
                project = await resolve_project(
                    session,
                    project_hint=project_name,
                    source_refs=[project_name],
                    create_if_missing=False,
                )
                if project:
                    project_note_id = project.id
            reminder = await store_reminder(
                session,
                raw_text=text,
                note_id=note.id,
                project_note_id=project_note_id,
                discord_channel_id=str(interaction.channel_id),
            )
        await interaction.followup.send(
            f"Reminder stored: {reminder.title} at {reminder.next_fire_at.isoformat() if reminder.next_fire_at else 'unscheduled'}"
        )

    @app_commands.command(name="remember", description="Save a quick note to your brain")
    @app_commands.describe(
        text="What do you want to remember?",
        category="Category for this note",
    )
    @app_commands.choices(
        category=[
            app_commands.Choice(name=cat.replace("_", " "), value=cat)
            for cat in BRAIN_CATEGORIES
        ]
    )
    async def remember(
        self,
        interaction: discord.Interaction,
        text: str,
        category: str | None = None,
    ):
        await interaction.response.defer(thinking=True)

        from src.worker.main import enqueue_ingest

        await enqueue_ingest(
            discord_message_id=None,
            discord_channel_id=str(interaction.channel_id),
            text=text,
            attachments=[],
            force_category=category,
            source="command",
        )

        await interaction.followup.send(f"Got it! Processing: *{text[:100]}*")

    @app_commands.command(name="task", description="Create a task")
    @app_commands.describe(text="What needs to be done?")
    async def task(self, interaction: discord.Interaction, text: str):
        await interaction.response.defer(thinking=True)

        from src.worker.main import enqueue_ingest

        await enqueue_ingest(
            discord_message_id=None,
            discord_channel_id=str(interaction.channel_id),
            text=text,
            attachments=[],
            force_category="task",
            source="command",
        )

        await interaction.followup.send(f"Task created: *{text[:100]}*")

    @app_commands.command(name="review", description="Show pending review items")
    async def review(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)

        async with async_session() as session:
            pending = await get_pending_reviews(session)

        if not pending:
            await interaction.followup.send("No pending reviews! Brain is all caught up.")
            return

        embed = discord.Embed(
            title=f"Pending Reviews ({len(pending)})",
            color=discord.Color.orange(),
        )

        for i, item in enumerate(pending[:10], 1):
            thread_link = f"<#{item.discord_thread_id}>" if item.discord_thread_id else "No thread"
            embed.add_field(
                name=f"{i}. {item.question[:80]}",
                value=thread_link,
                inline=False,
            )

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="story", description="Show the current story for a project")
    @app_commands.describe(project_name="Project title")
    async def story(self, interaction: discord.Interaction, project_name: str):
        await interaction.response.defer(thinking=True)

        async with async_session() as session:
            _project, payload, bootstrap = await _resolve_project_payload(
                session,
                project_name,
                session_id=f"discord:story:{interaction.id}",
            )
            if not payload:
                await interaction.followup.send(f"Project not found: {project_name}")
                return

        reboot = bootstrap.get("reboot_brief") or {}
        embed = discord.Embed(
            title=payload["project"]["title"],
            description=reboot.get("where_it_stands") or payload["project"]["content"] or "No project summary yet.",
            color=discord.Color.blue(),
        )
        if reboot.get("what_changed"):
            embed.add_field(name="What Changed", value=str(reboot["what_changed"])[:1024], inline=False)
        if reboot.get("what_is_left"):
            embed.add_field(name="What's Left", value=str(reboot["what_is_left"])[:1024], inline=False)
        if payload["repos"]:
            embed.add_field(
                name="Repos",
                value="\n".join(repo["name"] for repo in payload["repos"][:5]),
                inline=False,
            )
        if reboot.get("open_loops"):
            embed.add_field(
                name="Open Loops",
                value="\n".join(f"- {item}" for item in reboot["open_loops"][:5]),
                inline=False,
            )
        if payload["recent_activity"]:
            embed.add_field(
                name="Recent Activity",
                value="\n".join(entry["title"] for entry in payload["recent_activity"][:5]),
                inline=False,
            )
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="digest", description="Generate or refresh today's digest")
    async def digest(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)

        from datetime import datetime
        from zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo("America/New_York")).date()
        async with async_session() as session:
            payload = await generate_or_refresh_digest(session, digest_date=now, trigger="manual")

        embeds = build_digest_embeds({**payload, "trigger": "manual"})
        await interaction.followup.send(embeds=embeds)


async def setup(bot: commands.Bot):
    await bot.add_cog(CommandsCog(bot))
