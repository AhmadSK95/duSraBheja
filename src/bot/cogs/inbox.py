"""Inbox Cog — listens on #inbox, enqueues processing, handles review threads."""

import asyncio
import json
import logging

import discord
from redis.asyncio import Redis
from discord.ext import commands

from src.config import settings
from src.database import async_session
from src.lib.store import get_review_by_thread, resolve_review, set_review_thread

log = logging.getLogger("brain-bot.inbox")


class InboxCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._listener_task: asyncio.Task | None = None
        self._listener_stop = asyncio.Event()

    async def cog_load(self):
        self._listener_stop.clear()
        self._listener_task = asyncio.create_task(self._listen_notifications())

    def cog_unload(self):
        self._listener_stop.set()
        if self._listener_task:
            self._listener_task.cancel()

    def _is_inbox_channel(self, channel: discord.TextChannel) -> bool:
        return channel.name == settings.inbox_channel_name

    def _is_planner_channel(self, channel: discord.TextChannel) -> bool:
        return channel.name in {"daily-planner", "weekly-planner"}

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignore bot messages
        if message.author.bot:
            return

        # Handle review thread replies
        if isinstance(message.channel, discord.Thread):
            await self._handle_thread_reply(message)
            return

        # Handle #inbox messages
        if isinstance(message.channel, discord.TextChannel) and self._is_inbox_channel(message.channel):
            await self._handle_inbox_message(message)
            return

        # Handle #planner images (store only, no action)
        if isinstance(message.channel, discord.TextChannel) and self._is_planner_channel(message.channel):
            if message.attachments:
                await self._handle_planner_image(message)
            return

    async def _handle_inbox_message(self, message: discord.Message):
        """Process a new message in #inbox."""
        # React with brain emoji for immediate feedback
        await message.add_reaction("\U0001f9e0")

        # Collect attachment info
        attachments = []
        for att in message.attachments:
            attachments.append({
                "url": att.url,
                "filename": att.filename,
                "content_type": att.content_type or "application/octet-stream",
                "size": att.size,
            })

        # Enqueue ARQ job
        from src.worker.main import enqueue_ingest

        await enqueue_ingest(
            discord_message_id=str(message.id),
            discord_channel_id=str(message.channel.id),
            text=message.content,
            attachments=attachments,
        )

        log.info(f"Enqueued inbox message {message.id} ({len(attachments)} attachments)")

    async def _handle_planner_image(self, message: discord.Message):
        """Store planner images without further processing."""
        await message.add_reaction("\U0001f4c5")

        attachments = [
            {
                "url": att.url,
                "filename": att.filename,
                "content_type": att.content_type or "image/png",
                "size": att.size,
            }
            for att in message.attachments
            if att.content_type and att.content_type.startswith("image/")
        ]

        if attachments:
            from src.worker.main import enqueue_ingest

            await enqueue_ingest(
                discord_message_id=str(message.id),
                discord_channel_id=str(message.channel.id),
                text=message.content or f"[{message.channel.name} image]",
                attachments=attachments,
                force_category="daily_planner" if message.channel.name == "daily-planner" else "weekly_planner",
            )

    async def _handle_thread_reply(self, message: discord.Message):
        """Handle user replies in review threads."""
        thread_id = str(message.channel.id)

        async with async_session() as session:
            review = await get_review_by_thread(session, thread_id)
            if not review:
                return  # Not a tracked review thread

            # Store the answer and trigger re-classification
            await resolve_review(session, review.id, message.content)

            from src.worker.main import enqueue_reclassify

            await enqueue_reclassify(
                artifact_id=str(review.artifact_id),
                user_answer=message.content,
            )

            await message.add_reaction("\u2705")
            log.info(f"Review {review.id} answered in thread {thread_id}")

    async def _listen_notifications(self):
        await self.bot.wait_until_ready()
        redis = Redis.from_url(settings.redis_url)
        pubsub = redis.pubsub()
        await pubsub.subscribe("brain:artifact_processed", "brain:review_created")
        try:
            while not self._listener_stop.is_set():
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if not message:
                    await asyncio.sleep(0.1)
                    continue

                channel_name = message["channel"].decode()
                payload = json.loads(message["data"])
                if channel_name == "brain:artifact_processed":
                    await self._handle_artifact_processed(payload)
                elif channel_name == "brain:review_created":
                    await self._handle_review_created(payload)
        except asyncio.CancelledError:
            raise
        finally:
            await pubsub.unsubscribe("brain:artifact_processed", "brain:review_created")
            await pubsub.aclose()
            await redis.aclose()

    async def _handle_artifact_processed(self, payload: dict):
        channel_id = payload.get("discord_channel_id")
        message_id = payload.get("discord_message_id")
        if not channel_id or not message_id:
            return

        source_channel = await self.bot.fetch_channel(int(channel_id))
        if not isinstance(source_channel, discord.TextChannel):
            return

        source_message = await source_channel.fetch_message(int(message_id))
        await source_message.add_reaction("\u2705")

        receipt = discord.Embed(
            title=f"Stored as {payload['category'].replace('_', ' ').title()}",
            description=payload.get("summary") or payload.get("note_title") or "Stored in the brain.",
            color=discord.Color.green(),
        )
        receipt.add_field(name="Note", value=payload.get("note_title") or "Untitled", inline=False)
        receipt.add_field(name="Confidence", value=f"{payload.get('confidence', 0):.0%}", inline=True)
        receipt.add_field(name="Stored", value="Yes", inline=True)
        tags = payload.get("tags") or []
        if tags:
            receipt.add_field(name="Tags", value=", ".join(tags[:10]), inline=False)

        planner_message = None
        target_channel_name = payload.get("category_channel")
        if payload.get("category") in {"daily_planner", "weekly_planner"} and target_channel_name:
            target_channel = discord.utils.get(source_channel.guild.text_channels, name=target_channel_name)
            if target_channel:
                planner_card = discord.Embed(
                    title=payload.get("note_title") or payload["category"].replace("_", " ").title(),
                    description=(payload.get("note_content_preview") or payload.get("summary") or "Planner stored.")[:4000],
                    color=discord.Color.purple() if payload["category"] == "daily_planner" else discord.Color.dark_purple(),
                )
                planner_card.add_field(name="Type", value=payload["category"].replace("_", " ").title(), inline=True)
                planner_card.add_field(name="Status", value="Ingested", inline=True)
                planner_card.add_field(
                    name="Source",
                    value=f"[Open original]({source_message.jump_url})",
                    inline=False,
                )
                planner_message = await target_channel.send(embed=planner_card)
                await source_message.add_reaction("\U0001f4c5")

        if planner_message:
            receipt.add_field(
                name="Planner Card",
                value=f"[Open card]({planner_message.jump_url})",
                inline=False,
            )

        await source_message.reply(embed=receipt, mention_author=False)

    async def _handle_review_created(self, payload: dict):
        channel_id = payload.get("discord_channel_id")
        message_id = payload.get("discord_message_id")
        if not channel_id or not message_id:
            return

        channel = await self.bot.fetch_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            return

        message = await channel.fetch_message(int(message_id))
        thread = await message.create_thread(
            name=f"brain-review-{payload['review_id'][:8]}",
            auto_archive_duration=1440,
        )
        await thread.send(
            embed=discord.Embed(
                title="Need Clarification",
                description=payload["question"],
                color=discord.Color.orange(),
            )
        )
        await message.add_reaction("\u2753")

        async with async_session() as session:
            await set_review_thread(session, payload["review_id"], str(thread.id))


async def post_to_channel(
    bot: commands.Bot,
    guild_id: int,
    channel_name: str,
    embed: discord.Embed,
) -> discord.Message | None:
    """Post an embed to a named channel. Returns the posted message."""
    guild = bot.get_guild(guild_id)
    if not guild:
        log.error(f"Guild {guild_id} not found")
        return None

    channel = discord.utils.get(guild.text_channels, name=channel_name)
    if not channel:
        log.error(f"Channel #{channel_name} not found in guild {guild_id}")
        return None

    return await channel.send(embed=embed)


def build_classification_embed(classification: dict, summary: str, artifact_id: str) -> discord.Embed:
    """Build a rich embed for a classified item."""
    colors = {
        "task": discord.Color.red(),
        "project": discord.Color.blue(),
        "people": discord.Color.green(),
        "idea": discord.Color.gold(),
        "note": discord.Color.greyple(),
        "resource": discord.Color.brand_green(),
        "reminder": discord.Color.orange(),
        "daily_planner": discord.Color.purple(),
        "weekly_planner": discord.Color.dark_purple(),
    }

    category = classification["category"]
    embed = discord.Embed(
        title=classification.get("summary", summary[:100]),
        color=colors.get(category, discord.Color.default()),
    )
    embed.add_field(name="Category", value=category.title(), inline=True)
    embed.add_field(name="Priority", value=classification.get("priority", "medium").title(), inline=True)
    embed.add_field(
        name="Confidence",
        value=f"{classification.get('confidence', 0):.0%}",
        inline=True,
    )

    tags = classification.get("tags", [])
    if tags:
        embed.add_field(name="Tags", value=", ".join(tags), inline=False)

    entities = classification.get("entities", [])
    if entities:
        entity_str = ", ".join(f"{e['type']}: {e['value']}" for e in entities[:5])
        embed.add_field(name="Entities", value=entity_str, inline=False)

    if classification.get("suggested_action"):
        embed.add_field(name="Suggested Action", value=classification["suggested_action"], inline=False)

    embed.set_footer(text=f"ID: {artifact_id[:8]}")
    return embed


async def setup(bot: commands.Bot):
    await bot.add_cog(InboxCog(bot))
