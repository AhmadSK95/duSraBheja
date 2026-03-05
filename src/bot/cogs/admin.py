"""Admin Cog — /status, /stats slash commands."""

import logging

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import func, select

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


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))
