"""Inbox Cog — listens on #inbox, enqueues processing, handles review threads."""

import asyncio
import json
import logging

import discord
from redis.asyncio import Redis
from discord.ext import commands

from src.agents.retriever import answer_question
from src.config import settings
from src.database import async_session
from src.lib.store import get_artifact, get_review_by_thread, resolve_review, set_review_thread, update_artifact

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

    def _is_ask_channel(self, channel: discord.TextChannel) -> bool:
        return channel.name == settings.ask_channel_name

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

        if isinstance(message.channel, discord.TextChannel) and self._is_ask_channel(message.channel):
            await self._handle_ask_message(message)
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

    async def _handle_ask_message(self, message: discord.Message):
        question = (message.content or "").strip()
        if not question:
            if message.attachments:
                await message.reply(
                    "Use `#inbox` for files, audio, and links you want stored. Use this channel for questions.",
                    mention_author=False,
                )
            return

        await message.add_reaction("\U0001f914")
        try:
            async with message.channel.typing():
                async with async_session() as session:
                    result = await answer_question(session, question=question)

            embed = build_answer_embed(question, result)
            await message.reply(embed=embed, mention_author=False)
            await message.add_reaction("\u2705")
        except Exception as exc:
            log.exception("Failed to answer ask-brain message %s", message.id)
            await message.add_reaction("\u274c")
            await message.reply(
                embed=discord.Embed(
                    title="Brain Answer Failed",
                    description="I saw the question, but retrieval failed before I could answer. Try again in a moment.",
                    color=discord.Color.red(),
                ),
                mention_author=False,
            )

    async def _listen_notifications(self):
        await self.bot.wait_until_ready()
        redis = Redis.from_url(settings.redis_url)
        pubsub = redis.pubsub()
        await pubsub.subscribe(
            "brain:artifact_processed",
            "brain:review_created",
            "brain:artifact_failed",
            "brain:digest_ready",
            "brain:sync_completed",
        )
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
                elif channel_name == "brain:artifact_failed":
                    await self._handle_artifact_failed(payload)
                elif channel_name == "brain:digest_ready":
                    await self._handle_digest_ready(payload)
                elif channel_name == "brain:sync_completed":
                    await self._handle_sync_completed(payload)
        except asyncio.CancelledError:
            raise
        finally:
            await pubsub.unsubscribe(
                "brain:artifact_processed",
                "brain:review_created",
                "brain:artifact_failed",
                "brain:digest_ready",
                "brain:sync_completed",
            )
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

        planner = payload.get("planner") or {}
        weekly_rollup = payload.get("weekly_rollup") or {}
        receipt = discord.Embed(
            title="Brain Receipt",
            description=payload.get("summary") or payload.get("note_title") or "Stored in the brain.",
            color=discord.Color.green(),
        )
        receipt.add_field(name="Category", value=payload["category"].replace("_", " ").title(), inline=True)
        receipt.add_field(name="Confidence", value=f"{payload.get('confidence', 0):.0%}", inline=True)
        receipt.add_field(name="Stored", value="Yes", inline=True)
        receipt.add_field(name="Note", value=payload.get("note_title") or "Untitled", inline=False)
        receipt.add_field(
            name="Pipeline",
            value="Ingested -> Classified -> Stored",
            inline=False,
        )
        tags = payload.get("tags") or []
        if tags:
            receipt.add_field(name="Tags", value=", ".join(tags[:10]), inline=False)
        if planner.get("dates"):
            receipt.add_field(name="Planner Dates", value=_format_list(planner["dates"]), inline=False)
        if planner.get("top_items"):
            receipt.add_field(name="Top Items", value=_format_list(planner["top_items"]), inline=False)

        planner_message = None
        weekly_message = None
        target_channel_name = payload.get("category_channel")
        if payload.get("category") in {"daily_planner", "weekly_planner"} and target_channel_name:
            target_channel = discord.utils.get(source_channel.guild.text_channels, name=target_channel_name)
            if target_channel:
                planner_card = discord.Embed(
                    title=planner.get("title") or payload.get("note_title") or payload["category"].replace("_", " ").title(),
                    description=(planner.get("summary") or payload.get("summary") or "Planner stored.")[:4000],
                    color=discord.Color.purple() if payload["category"] == "daily_planner" else discord.Color.dark_purple(),
                )
                planner_card.add_field(name="Type", value=payload["category"].replace("_", " ").title(), inline=True)
                planner_card.add_field(name="Status", value="Ingested", inline=True)
                if planner.get("dates"):
                    planner_card.add_field(name="Dates", value=_format_list(planner["dates"]), inline=False)
                if planner.get("top_items"):
                    planner_card.add_field(name="Top Items", value=_format_list(planner["top_items"]), inline=False)
                if planner.get("focus_projects"):
                    planner_card.add_field(name="Projects", value=_format_list(planner["focus_projects"]), inline=False)
                if planner.get("focus_people"):
                    planner_card.add_field(name="People", value=_format_list(planner["focus_people"]), inline=False)
                planner_card.add_field(
                    name="Source",
                    value=f"[Open original]({source_message.jump_url})",
                    inline=False,
                )
                planner_message = await target_channel.send(embed=planner_card)
                await source_message.add_reaction("\U0001f4c5")

        if weekly_rollup:
            weekly_channel = discord.utils.get(source_channel.guild.text_channels, name="weekly-planner")
            if weekly_channel:
                weekly_card = discord.Embed(
                    title=weekly_rollup.get("title") or "Weekly Rollup",
                    description=weekly_rollup.get("summary") or "Weekly planner rollup updated.",
                    color=discord.Color.dark_purple(),
                )
                if weekly_rollup.get("dates"):
                    weekly_card.add_field(name="Dates", value=_format_list(weekly_rollup["dates"]), inline=False)
                if weekly_rollup.get("top_items"):
                    weekly_card.add_field(name="Top Items", value=_format_list(weekly_rollup["top_items"]), inline=False)
                weekly_card.add_field(name="Source", value=f"[Open original]({source_message.jump_url})", inline=False)
                weekly_message = await weekly_channel.send(embed=weekly_card)

        if planner_message:
            receipt.add_field(
                name="Planner Card",
                value=f"[Open card]({planner_message.jump_url})",
                inline=False,
            )
        if weekly_message:
            receipt.add_field(
                name="Weekly Rollup",
                value=f"[Open card]({weekly_message.jump_url})",
                inline=False,
            )

        receipt_message = await source_message.reply(embed=receipt, mention_author=False)

        artifact_id = payload.get("artifact_id")
        if artifact_id:
            await self._store_artifact_outputs(
                artifact_id,
                receipt_message=receipt_message,
                planner_message=planner_message,
                weekly_message=weekly_message,
            )

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

    async def _handle_artifact_failed(self, payload: dict):
        channel_id = payload.get("discord_channel_id")
        message_id = payload.get("discord_message_id")
        if not channel_id or not message_id:
            return

        channel = await self.bot.fetch_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            return

        message = await channel.fetch_message(int(message_id))
        await message.add_reaction("\u274c")
        embed = discord.Embed(
            title="Brain Processing Failed",
            description="This message was seen, but it was not fully stored yet.",
            color=discord.Color.red(),
        )
        embed.add_field(name="Stage", value=str(payload.get("stage") or "unknown").title(), inline=True)
        embed.add_field(name="Stored", value="No", inline=True)
        embed.add_field(name="Error", value=(payload.get("error") or "Unknown error")[:1000], inline=False)
        await message.reply(embed=embed, mention_author=False)

    async def _handle_digest_ready(self, payload: dict):
        task_titles = [task["title"] for task in payload.get("tasks", [])[:5]] or ["No active tasks"]
        project_titles = [project["title"] for project in payload.get("projects", [])[:5]] or ["No active projects"]
        recent_titles = [
            f"{entry.get('title')} ({entry.get('actor_name') or 'unknown'})"
            for entry in payload.get("recent_activity", [])[:5]
        ] or ["No recent activity"]

        embed = discord.Embed(
            title=f"Daily Digest - {payload.get('digest_date')}",
            description="Fresh morning snapshot from the brain.",
            color=discord.Color.gold(),
        )
        embed.add_field(name="Tasks", value="\n".join(task_titles), inline=False)
        embed.add_field(name="Projects", value="\n".join(project_titles), inline=False)
        embed.add_field(name="Recent Activity", value="\n".join(recent_titles), inline=False)

        pending_reviews = payload.get("pending_reviews") or []
        if pending_reviews:
            embed.add_field(
                name="Pending Reviews",
                value="\n".join(item["question"] for item in pending_reviews[:3]),
                inline=False,
            )

        writing_topics = payload.get("writing_topics") or []
        if writing_topics:
            embed.add_field(name="Writing Topics", value="\n".join(writing_topics[:5]), inline=False)

        await post_to_channel(
            self.bot,
            settings.discord_guild_id,
            settings.daily_digest_channel_name,
            embed,
        )

    async def _handle_sync_completed(self, payload: dict):
        status = (payload.get("status") or "completed").lower()
        color = discord.Color.gold() if status == "noop" else discord.Color.green()
        embed = discord.Embed(
            title="Brain Sync Receipt",
            description=f"{payload.get('source_name') or 'sync'} {status}.",
            color=color,
        )
        embed.add_field(name="Mode", value=str(payload.get("mode") or "sync").title(), inline=True)
        embed.add_field(name="Seen", value=str(payload.get("items_seen") or 0), inline=True)
        embed.add_field(name="Imported", value=str(payload.get("items_imported") or 0), inline=True)

        if payload.get("device_name"):
            embed.add_field(name="Device", value=str(payload["device_name"]), inline=True)
        if payload.get("source_type"):
            embed.add_field(name="Source", value=str(payload["source_type"]).title(), inline=True)
        if payload.get("sync_run_id"):
            embed.set_footer(text=f"Sync run: {payload['sync_run_id'][:8]}")

        await post_to_channel(
            self.bot,
            settings.discord_guild_id,
            settings.daily_digest_channel_name,
            embed,
        )

    async def _store_artifact_outputs(
        self,
        artifact_id: str,
        *,
        receipt_message: discord.Message | None = None,
        planner_message: discord.Message | None = None,
        weekly_message: discord.Message | None = None,
    ) -> None:
        metadata_updates = {}
        if receipt_message:
            metadata_updates["discord_receipt_message_id"] = str(receipt_message.id)
        if planner_message:
            metadata_updates["discord_planner_card_channel_id"] = str(planner_message.channel.id)
            metadata_updates["discord_planner_card_message_id"] = str(planner_message.id)
        if weekly_message:
            metadata_updates["discord_weekly_rollup_channel_id"] = str(weekly_message.channel.id)
            metadata_updates["discord_weekly_rollup_message_id"] = str(weekly_message.id)

        if not metadata_updates:
            return

        async with async_session() as session:
            from uuid import UUID

            artifact_uuid = UUID(artifact_id)
            artifact = await get_artifact(session, artifact_uuid)
            merged = dict(artifact.metadata_ or {}) if artifact else {}
            merged.update(metadata_updates)
            await update_artifact(session, artifact_uuid, metadata_=merged)


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


def _format_list(values: list[str], *, limit: int = 6) -> str:
    trimmed = [value for value in values if value][:limit]
    if not trimmed:
        return "None"
    return "\n".join(f"- {value[:120]}" for value in trimmed)


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


def build_answer_embed(question: str, result: dict) -> discord.Embed:
    model_label = result.get("model") or "unknown"
    if "-" in model_label:
        parts = [part for part in model_label.split("-") if part]
        model_label = parts[1].title() if len(parts) > 1 else parts[0].title()
    else:
        model_label = model_label.title()

    embed = discord.Embed(
        title="Brain Answer",
        description=result["answer"],
        color=discord.Color.teal(),
    )
    embed.add_field(name="Question", value=question[:1024], inline=False)

    sources = result.get("sources") or []
    if sources:
        source_lines = [
            f"[{i}] {src['category']}: {src['title']} ({src['similarity']:.0%})"
            for i, src in enumerate(sources[:5], 1)
        ]
        embed.add_field(name="Sources", value="\n".join(source_lines), inline=False)

    embed.add_field(name="Confidence", value=result["confidence"].title(), inline=True)
    embed.add_field(name="Model", value=model_label, inline=True)
    return embed


async def setup(bot: commands.Bot):
    await bot.add_cog(InboxCog(bot))
