"""Commands Cog — /ask, /remember, /task, /review slash commands."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from src.config import settings
from src.database import async_session
from src.agents.retriever import answer_question
from src.lib.store import get_pending_reviews

log = logging.getLogger("brain-bot.commands")


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
            app_commands.Choice(name=cat, value=cat)
            for cat in ["task", "project", "people", "idea", "note", "reminder", "planner"]
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
            result = await answer_question(
                session,
                question=question,
                category=category,
                use_opus=deep,
            )

        embed = discord.Embed(
            title="Brain Answer",
            description=result["answer"],
            color=discord.Color.teal(),
        )

        if result["sources"]:
            source_lines = []
            for i, src in enumerate(result["sources"], 1):
                source_lines.append(f"[{i}] {src['category']}: {src['title']} ({src['similarity']:.0%})")
            embed.add_field(name="Sources", value="\n".join(source_lines[:5]), inline=False)

        embed.add_field(name="Confidence", value=result["confidence"].title(), inline=True)
        embed.add_field(name="Model", value=result["model"].split("-")[1].title(), inline=True)

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="remember", description="Save a quick note to your brain")
    @app_commands.describe(
        text="What do you want to remember?",
        category="Category for this note",
    )
    @app_commands.choices(
        category=[
            app_commands.Choice(name=cat, value=cat)
            for cat in ["task", "project", "people", "idea", "note", "reminder", "planner"]
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


async def setup(bot: commands.Bot):
    await bot.add_cog(CommandsCog(bot))
