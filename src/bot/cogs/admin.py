"""Admin Cog — status, stats, and safe cleanup slash commands."""

import logging

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import func, select

from src.bot.cleanup import collect_target_channels, purge_bot_messages
from src.bot.replay import replay_discord_history
from src.database import async_session
from src.models import Artifact, Classification, Note, AuditLog, ReviewQueue

log = logging.getLogger("brain-bot.admin")


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="status", description="Brain system status")
    async def status(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)

        async with async_session() as session:
            artifacts = (await session.execute(select(func.count(Artifact.id)))).scalar()
            notes = (await session.execute(select(func.count(Note.id)))).scalar()
            pending = (
                await session.execute(
                    select(func.count(ReviewQueue.id)).where(ReviewQueue.status == "pending")
                )
            ).scalar()
            total_cost = (
                await session.execute(select(func.sum(AuditLog.cost_usd)))
            ).scalar() or 0

        embed = discord.Embed(title="Brain Status", color=discord.Color.green())
        embed.add_field(name="Artifacts", value=str(artifacts), inline=True)
        embed.add_field(name="Notes", value=str(notes), inline=True)
        embed.add_field(name="Pending Reviews", value=str(pending), inline=True)
        embed.add_field(name="Total AI Cost", value=f"${float(total_cost):.4f}", inline=True)
        embed.set_footer(text="duSraBheja v2")

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="stats", description="Classification breakdown")
    async def stats(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)

        async with async_session() as session:
            result = await session.execute(
                select(Classification.category, func.count(Classification.id))
                .where(Classification.is_final == True)
                .group_by(Classification.category)
            )
            rows = result.all()

        embed = discord.Embed(title="Classification Stats", color=discord.Color.blue())
        for category, count in sorted(rows, key=lambda x: x[1], reverse=True):
            embed.add_field(name=category.title(), value=str(count), inline=True)

        if not rows:
            embed.description = "No classifications yet. Drop something in #inbox!"

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="cleanup-bot-posts", description="Delete this bot's posts from a channel for testing")
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.describe(
        channel="Channel to clean. Defaults to the current channel.",
        dry_run="Preview how many bot posts would be deleted without deleting them.",
        include_threads="Also clean active threads under the chosen channel.",
        history_limit="How many recent messages to scan. Leave empty to scan the full history.",
    )
    async def cleanup_bot_posts(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
        dry_run: bool = True,
        include_threads: bool = True,
        history_limit: app_commands.Range[int, 1, 5000] | None = None,
    ):
        await interaction.response.defer(thinking=True, ephemeral=True)

        if not interaction.guild or not self.bot.user:
            await interaction.followup.send("This command only works inside a server.", ephemeral=True)
            return

        target_channel = channel or interaction.channel
        if not isinstance(target_channel, discord.TextChannel):
            await interaction.followup.send("Pick a standard text channel for cleanup.", ephemeral=True)
            return

        channels = await collect_target_channels(
            interaction.guild,
            channel_names=[target_channel.name],
            include_threads=include_threads,
            include_archived_threads=False,
        )
        result = await purge_bot_messages(
            channels=[item for item in channels if isinstance(item, (discord.TextChannel, discord.Thread))],
            bot_user_id=self.bot.user.id,
            dry_run=dry_run,
            history_limit=history_limit,
        )

        embed = discord.Embed(
            title="Bot Post Cleanup",
            description="Dry run only." if dry_run else "Bot-authored posts removed.",
            color=discord.Color.orange() if dry_run else discord.Color.red(),
        )
        embed.add_field(name="Messages Matched", value=str(result["messages_deleted"]), inline=True)
        embed.add_field(name="Messages Scanned", value=str(result["messages_scanned"]), inline=True)
        embed.add_field(name="Channels Scanned", value=str(result["channels_scanned"]), inline=True)
        details = []
        for item in result["channels"][:8]:
            suffix = f" ({', '.join(item['errors'])})" if item["errors"] else ""
            details.append(f"- {item['channel_name']}: {item['deleted_count']}{suffix}")
        if details:
            embed.add_field(name="Channels", value="\n".join(details), inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="replay-ingestion", description="Replay your Discord posts so receipts and planner cards are rebuilt")
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.describe(
        history_limit="How many recent messages per replay channel to scan. Leave empty to scan the full history.",
    )
    async def replay_ingestion(
        self,
        interaction: discord.Interaction,
        history_limit: app_commands.Range[int, 1, 5000] | None = None,
    ):
        await interaction.response.defer(thinking=True, ephemeral=True)

        stats = await replay_discord_history(
            self.bot,
            history_limit=history_limit,
        )
        embed = discord.Embed(
            title="Discord Replay",
            description="Scanned the configured brain channels and repaired missing ingestion outputs.",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Scanned", value=str(stats.scanned_messages), inline=True)
        embed.add_field(name="Queued New", value=str(stats.queued_new), inline=True)
        embed.add_field(name="Requeued Existing", value=str(stats.requeued_existing), inline=True)
        embed.add_field(name="Skipped Existing", value=str(stats.skipped_existing), inline=True)
        if stats.channel_counts:
            embed.add_field(
                name="Channels",
                value="\n".join(f"- {name}: {count}" for name, count in stats.channel_counts.items()),
                inline=False,
            )
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))
