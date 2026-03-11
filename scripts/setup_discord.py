"""One-time script to create Discord channels for the brain.

Usage: python -m scripts.setup_discord
Requires DISCORD_TOKEN and DISCORD_GUILD_ID in .env
"""

import asyncio
import discord
from src.config import settings

CHANNELS = [
    ("inbox", "Drop anything here — the brain will classify and route it"),
    ("tasks", "Actionable items with deadlines"),
    ("projects", "Multi-step initiatives and ongoing efforts"),
    ("people", "Contacts and notes about people"),
    ("ideas", "Brainstorms, concepts, and creative thoughts"),
    ("notes", "General knowledge and durable notes"),
    ("resources", "References, guides, links, and reusable assets"),
    ("reminders", "Time-bound alerts"),
    ("daily-planner", "Daily planning entries and screenshots"),
    ("weekly-planner", "Weekly planning entries and screenshots"),
    ("needs-review", "Items that need your clarification"),
    ("daily-digest", "Morning digest with tasks, progress, and recommendations"),
    ("ask-brain", "Ask questions and get answers from your brain"),
]


async def main():
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        guild = client.get_guild(settings.discord_guild_id)
        if not guild:
            print(f"Guild {settings.discord_guild_id} not found!")
            await client.close()
            return

        existing = {ch.name for ch in guild.text_channels}
        print(f"Guild: {guild.name}")
        print(f"Existing channels: {existing}")

        # Create a category for brain channels
        category = discord.utils.get(guild.categories, name="BRAIN")
        if not category:
            category = await guild.create_category("BRAIN")
            print(f"Created category: BRAIN")

        for name, topic in CHANNELS:
            if name in existing:
                print(f"  #{name} already exists, skipping")
                continue
            await guild.create_text_channel(name, category=category, topic=topic)
            print(f"  Created #{name}")

        print("\nDone! All channels created.")
        await client.close()

    await client.start(settings.discord_token)


if __name__ == "__main__":
    asyncio.run(main())
