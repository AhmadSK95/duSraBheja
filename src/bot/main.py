"""Discord bot entrypoint — loads Cogs and connects."""

import asyncio
import logging

import discord
from discord.ext import commands

from src.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
log = logging.getLogger("brain-bot")

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = False

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    log.info(f"Brain bot connected as {bot.user} (ID: {bot.user.id})")
    log.info(f"Guild: {settings.discord_guild_id}")

    # Sync slash commands
    guild = discord.Object(id=settings.discord_guild_id)
    bot.tree.copy_global_to(guild=guild)
    synced = await bot.tree.sync(guild=guild)
    log.info(f"Synced {len(synced)} slash commands")


async def load_cogs():
    await bot.load_extension("src.bot.cogs.inbox")
    await bot.load_extension("src.bot.cogs.commands")
    await bot.load_extension("src.bot.cogs.admin")
    log.info("All cogs loaded")


async def main():
    async with bot:
        await load_cogs()
        await bot.start(settings.discord_token)


if __name__ == "__main__":
    asyncio.run(main())
