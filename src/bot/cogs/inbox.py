"""Inbox Cog — stores captures silently and publishes only boards, digest, answers, and reminders."""

import asyncio
import json
import logging
import re
from uuid import UUID

import discord
from redis.asyncio import Redis
from discord.ext import commands

from src.agents.retriever import answer_question
from src.bot.replay import replay_discord_history
from src.config import settings
from src.database import async_session
from src.lib.store import get_artifact, get_review_by_thread, resolve_review, update_artifact, update_retrieval_trace
from src.services.secrets import capture_secret_drop, extract_secret_candidates

log = logging.getLogger("brain-bot.inbox")


class InboxCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._listener_task: asyncio.Task | None = None
        self._startup_replay_task: asyncio.Task | None = None
        self._listener_stop = asyncio.Event()

    async def cog_load(self):
        self._listener_stop.clear()
        self._listener_task = asyncio.create_task(self._listen_notifications())
        if settings.startup_replay_enabled:
            self._startup_replay_task = asyncio.create_task(self._run_startup_replay())

    def cog_unload(self):
        self._listener_stop.set()
        if self._listener_task:
            self._listener_task.cancel()
        if self._startup_replay_task:
            self._startup_replay_task.cancel()

    def _is_inbox_channel(self, channel: discord.TextChannel) -> bool:
        return channel.name == settings.inbox_channel_name

    def _is_planner_channel(self, channel: discord.TextChannel) -> bool:
        return channel.name in {
            settings.daily_board_channel_name,
            settings.weekly_board_channel_name,
            "daily-planner",
            "weekly-planner",
        }

    def _is_ask_channel(self, channel: discord.TextChannel) -> bool:
        return channel.name == settings.ask_channel_name

    def _is_digest_channel(self, channel: discord.TextChannel) -> bool:
        return channel.name == settings.daily_digest_channel_name

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignore bot messages
        if message.author.bot:
            return

        if isinstance(message.channel, discord.DMChannel):
            await self._handle_direct_message(message)
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

        if isinstance(message.channel, discord.TextChannel) and (
            self._is_planner_channel(message.channel) or self._is_digest_channel(message.channel)
        ):
            await self._handle_feedback_capture(message)
            return

    async def _handle_direct_message(self, message: discord.Message):
        if not settings.discord_owner_user_id or message.author.id != settings.discord_owner_user_id:
            await message.reply(
                "This DM path is reserved for the owner vault workflow only.",
                mention_author=False,
            )
            return

        body = (message.content or "").strip()
        if not body and not message.attachments:
            return

        if not _looks_like_secret_drop(body):
            await message.reply(
                "Use this DM for secret drops. Prefix with `vault:` or paste the credential directly with a label, and I’ll store it in the vault.",
                mention_author=False,
            )
            return

        async with async_session() as session:
            records, _ = await capture_secret_drop(
                session,
                text=body,
                source_kind="discord_dm",
                source_ref=f"discord-dm:{message.author.id}:{message.id}",
                purpose_label="Owner Discord DM secret drop",
                alias_hints=["discord-dm", message.author.display_name or message.author.name],
            )

        if records:
            await message.add_reaction("\U0001f512")
            await message.reply(
                f"Stored {len(records)} secret entr{'y' if len(records) == 1 else 'ies'} in the owner vault. Reveal still requires dashboard login plus a fresh Discord OTP challenge.",
                mention_author=False,
            )
            return

        await message.reply(
            "I didn’t find a storable secret there. Try `vault: label = value` so I can vault it cleanly.",
            mention_author=False,
        )

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

        explicit_hint = _extract_capture_hint(message.content or "")
        await enqueue_ingest(
            discord_message_id=str(message.id),
            discord_channel_id=str(message.channel.id),
            text=message.content,
            attachments=attachments,
            force_category=explicit_hint,
            metadata={
                "channel_name": message.channel.name,
                "ingest_hint": explicit_hint,
                "capture_context": "inbox",
                **_reply_target_metadata(message),
            },
        )

        log.info(f"Enqueued inbox message {message.id} ({len(attachments)} attachments)")

    async def _handle_feedback_capture(self, message: discord.Message):
        """Store replies or comments on generated board/digest channels as silent feedback captures."""
        attachments = [
            {
                "url": att.url,
                "filename": att.filename,
                "content_type": att.content_type or "application/octet-stream",
                "size": att.size,
            }
            for att in message.attachments
        ]
        if not attachments and not (message.content or "").strip():
            return

        await message.add_reaction("\U0001f9e0")
        from src.worker.main import enqueue_ingest

        explicit_hint = _extract_capture_hint(message.content or "")
        await enqueue_ingest(
            discord_message_id=str(message.id),
            discord_channel_id=str(message.channel.id),
            text=message.content or f"[{message.channel.name} feedback]",
            attachments=attachments,
            force_category=explicit_hint,
            metadata={
                "channel_name": message.channel.name,
                "ingest_hint": explicit_hint,
                "capture_context": "feedback",
                **_reply_target_metadata(message),
            },
        )
        log.info("Enqueued feedback capture %s from #%s", message.id, message.channel.name)

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

        if _looks_like_reminder_request(question):
            from src.worker.main import enqueue_ingest

            await message.add_reaction("\U0001f4cc")
            await enqueue_ingest(
                discord_message_id=None,
                discord_channel_id=str(message.channel.id),
                text=question,
                attachments=[],
                force_category="reminder",
                source="ask-brain",
            )
            await message.reply(
                "Captured that as a reminder request. I’ll store it and schedule the Discord reminder.",
                mention_author=False,
            )
            return

        await message.add_reaction("\U0001f914")
        try:
            async with message.channel.typing():
                async with async_session() as session:
                    result = await answer_question(session, question=question)

            if not result.get("ok", True):
                await message.add_reaction("\u274c")
                await message.reply(
                    embed=build_answer_failure_embed(result),
                    mention_author=False,
                )
                return

            try:
                text_reply = build_answer_text(question, result)
                if len(text_reply) <= 1900:
                    await message.reply(text_reply, mention_author=False)
                else:
                    await message.reply(embed=build_answer_embed(question, result), mention_author=False)
            except Exception:
                log.exception("Embed send failed for ask-brain message %s; falling back to text", message.id)
                trace_id = result.get("retrieval_trace_id")
                if trace_id:
                    async with async_session() as session:
                        await update_retrieval_trace(
                            session,
                            UUID(trace_id),
                            failure_stage="render_send",
                            payload={"render_fallback_used": True},
                        )
                await message.reply(build_answer_text(question, result), mention_author=False)
            await message.add_reaction("\u2705")
        except Exception:
            log.exception("Failed to answer ask-brain message %s", message.id)
            await message.add_reaction("\u274c")
            await message.reply(
                embed=discord.Embed(
                    title="Brain Answer Failed",
                    description="I saw the question, but hit a render or answer stage failure before I could reply cleanly. Try again in a moment.",
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
            "brain:artifact_failed",
            "brain:board_ready",
            "brain:digest_ready",
            "brain:sync_completed",
            "brain:reminder_due",
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
                elif channel_name == "brain:artifact_failed":
                    await self._handle_artifact_failed(payload)
                elif channel_name == "brain:board_ready":
                    await self._handle_board_ready(payload)
                elif channel_name == "brain:digest_ready":
                    await self._handle_digest_ready(payload)
                elif channel_name == "brain:sync_completed":
                    await self._handle_sync_completed(payload)
                elif channel_name == "brain:reminder_due":
                    await self._handle_reminder_due(payload)
        except asyncio.CancelledError:
            raise
        finally:
            await pubsub.unsubscribe(
                "brain:artifact_processed",
                "brain:artifact_failed",
                "brain:board_ready",
                "brain:digest_ready",
                "brain:sync_completed",
                "brain:reminder_due",
            )
            await pubsub.aclose()
            await redis.aclose()

    async def _run_startup_replay(self):
        await self.bot.wait_until_ready()
        try:
            stats = await replay_discord_history(
                self.bot,
                history_limit=settings.startup_replay_history_limit or None,
            )
            log.info("Startup replay stats: %s", stats.as_dict())
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Startup replay failed")

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

    async def _handle_review_created(self, payload: dict):
        log.info("Review created for moderation queue: %s", payload.get("review_id"))

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

    async def _handle_board_ready(self, payload: dict):
        embed = build_board_embed(payload)
        channel_name = payload.get("channel_name") or (
            settings.daily_board_channel_name if payload.get("board_type") == "daily" else settings.weekly_board_channel_name
        )
        await post_to_channel(
            self.bot,
            settings.discord_guild_id,
            channel_name,
            embed,
            fallback_names=_legacy_board_fallback_names(channel_name),
        )

    async def _handle_digest_ready(self, payload: dict):
        for embed in build_digest_embeds(payload):
            await post_to_channel(
                self.bot,
                settings.discord_guild_id,
                settings.daily_digest_channel_name,
                embed,
            )

    async def _handle_sync_completed(self, payload: dict):
        log.info(
            "Sync completed: %s status=%s seen=%s imported=%s",
            payload.get("source_name") or payload.get("source_type") or "sync",
            payload.get("status"),
            payload.get("items_seen"),
            payload.get("items_imported"),
        )

    async def _handle_reminder_due(self, payload: dict):
        embed = discord.Embed(
            title="Brain Reminder",
            description=payload.get("title") or "Reminder due.",
            color=discord.Color.orange(),
        )
        if payload.get("body"):
            embed.add_field(name="Details", value=str(payload["body"])[:1024], inline=False)
        if payload.get("project_ref"):
            embed.add_field(name="Project", value=str(payload["project_ref"]), inline=True)
        if payload.get("next_fire_at"):
            embed.add_field(name="Next", value=str(payload["next_fire_at"]), inline=True)

        channel_name = settings.daily_digest_channel_name
        if payload.get("discord_channel_id"):
            guild = self.bot.get_guild(settings.discord_guild_id)
            if guild:
                channel = guild.get_channel(int(payload["discord_channel_id"]))
                if isinstance(channel, discord.TextChannel):
                    await channel.send(embed=embed)
                    return
        await post_to_channel(self.bot, settings.discord_guild_id, channel_name, embed)

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
    *,
    fallback_names: tuple[str, ...] = (),
) -> discord.Message | None:
    """Post an embed to a named channel. Returns the posted message."""
    guild = bot.get_guild(guild_id)
    if not guild:
        log.error(f"Guild {guild_id} not found")
        return None

    channel = discord.utils.get(guild.text_channels, name=channel_name)
    if not channel:
        for fallback in fallback_names:
            channel = discord.utils.get(guild.text_channels, name=fallback)
            if channel:
                break
    if not channel:
        names = ", ".join((channel_name, *fallback_names))
        log.error(f"Channel(s) #{names} not found in guild {guild_id}")
        return None

    return await channel.send(embed=embed)


def _format_list(values: list[str], *, limit: int = 6) -> str:
    trimmed = [value for value in values if value][:limit]
    if not trimmed:
        return "None"
    return "\n".join(f"- {value[:120]}" for value in trimmed)


def _looks_like_reminder_request(text: str) -> bool:
    lowered = (text or "").lower()
    return bool(
        lowered.startswith("remind me")
        or lowered.startswith("set a reminder")
        or re.search(r"\bevery\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", lowered)
    )


def _looks_like_secret_drop(text: str) -> bool:
    lowered = (text or "").strip().lower()
    if not lowered:
        return False
    if lowered.startswith(("vault:", "secret:", "/vault", "/secret", "store secret")):
        return True
    return bool(extract_secret_candidates(text or ""))


def _extract_capture_hint(text: str) -> str | None:
    lowered = (text or "").lower()
    if "#daily-plan" in lowered:
        return "daily_planner"
    if "#weekly-plan" in lowered:
        return "weekly_planner"
    return None


def _reply_target_metadata(message: discord.Message) -> dict:
    reference = getattr(message, "reference", None)
    resolved = getattr(reference, "resolved", None)
    if not isinstance(resolved, discord.Message):
        return {}

    channel_name = getattr(message.channel, "name", "")
    if channel_name in {settings.daily_board_channel_name, "daily-planner"}:
        target_kind = "daily_board"
    elif channel_name in {settings.weekly_board_channel_name, "weekly-planner"}:
        target_kind = "weekly_board"
    elif channel_name == settings.daily_digest_channel_name:
        target_kind = "daily_digest"
    else:
        target_kind = "message"

    return {
        "reply_target_kind": target_kind,
        "reply_target_message_id": str(resolved.id),
        "reply_target_author_id": str(resolved.author.id),
        "reply_target_channel_id": str(resolved.channel.id),
    }


def _legacy_board_fallback_names(channel_name: str) -> tuple[str, ...]:
    if channel_name == settings.daily_board_channel_name:
        return ("daily-planner",)
    if channel_name == settings.weekly_board_channel_name:
        return ("weekly-planner",)
    return ()


def _format_digest_entries(values: list[str], *, limit: int = 5, max_line: int = 180) -> str:
    trimmed = [value for value in values if value][:limit]
    if not trimmed:
        return "None"
    rendered = "\n".join(f"- {value[:max_line]}" for value in trimmed)
    if len(rendered) <= 1024:
        return rendered
    return rendered[:1021].rstrip() + "..."


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
    embed = discord.Embed(
        title="Here’s the read",
        description=str(result["answer"])[:4000],
        color=discord.Color.teal(),
    )
    brain_sources = result.get("brain_sources") or []
    if brain_sources:
        source_lines = [
            f"[{i}] {src['title']}"
            for i, src in enumerate(brain_sources[:5], 1)
        ]
        embed.add_field(name="Grounding", value="\n".join(source_lines)[:1024], inline=False)

    web_sources = result.get("web_sources") or []
    if web_sources:
        web_lines = [
            f"[{i}] {src.get('title') or src.get('source_hint') or 'Web result'}"
            for i, src in enumerate(web_sources[:4], 1)
        ]
        embed.add_field(name="Outside Context", value="\n".join(web_lines)[:1024], inline=False)
    embed.set_footer(
        text=(
            f"Grounded in {len(brain_sources)} brain signals"
            + (f" • {len(web_sources)} outside references" if web_sources else "")
        )
    )
    return embed


def build_answer_text(question: str, result: dict) -> str:
    lines = [str(result.get("answer") or "").strip()]
    if result.get("brain_sources"):
        lines.append("")
        lines.append("Grounding:")
        for index, src in enumerate((result.get("brain_sources") or [])[:5], 1):
            lines.append(f"[{index}] {src.get('title')}")
    if result.get("web_sources"):
        lines.append("")
        lines.append("Outside context:")
        for index, src in enumerate((result.get("web_sources") or [])[:4], 1):
            lines.append(f"[{index}] {src.get('title') or src.get('source_hint') or 'Web result'}")
    rendered = "\n".join(lines).strip()
    return rendered[:1900]


def build_answer_failure_embed(result: dict) -> discord.Embed:
    stage = str(result.get("failure_stage") or "answering").replace("_", " ")
    trace_id = result.get("retrieval_trace_id")
    embed = discord.Embed(
        title="Brain Answer Unavailable",
        description=str(result.get("answer") or f"I hit a {stage} issue before I could answer cleanly.")[:4000],
        color=discord.Color.red(),
    )
    embed.add_field(name="Failure Stage", value=stage.title(), inline=True)
    if trace_id:
        embed.add_field(name="Trace", value=str(trace_id)[:32], inline=True)
    if result.get("mode"):
        embed.add_field(name="Mode", value=str(result["mode"]).replace("_", " ").title(), inline=True)
    return embed


def build_digest_embeds(payload: dict) -> list[discord.Embed]:
    embed = discord.Embed(
        title=f"Daily Digest - {payload.get('digest_date')}",
        description=(payload.get("summary") or payload.get("headline") or "Morning operating brief.")[:4000],
        color=discord.Color.gold(),
    )
    project_status = payload.get("project_status") or []
    if project_status:
        embed.add_field(
            name="Project Status",
            value=_format_digest_entries(
                [
                    (
                        f"{item.get('project')}: {item.get('where_it_stands')} "
                        f"| Changed: {item.get('what_changed')} "
                        f"| Blocked: {item.get('blocked_or_unclear')} "
                        f"| Next: {item.get('best_next_move')}"
                    )
                    for item in project_status[:5]
                ],
                limit=5,
                max_line=220,
            ),
            inline=False,
        )
    task_lines = [
        f"{item.get('title')} — {item.get('why')}"
        for item in (payload.get("possible_tasks") or [])[:8]
    ] or ["No clear task suggestions yet."]
    embed.add_field(
        name="Possible Task List",
        value=_format_digest_entries(task_lines, limit=8, max_line=180),
        inline=False,
    )
    reminder_lines = [
        f"{item.get('title')} — {item.get('next_fire_at') or 'today'}"
        for item in (payload.get("reminders_due_today") or [])[:8]
    ] or ["No reminders due today."]
    embed.add_field(
        name="Reminders",
        value=_format_digest_entries(reminder_lines, limit=8, max_line=160),
        inline=False,
    )
    if payload.get("board_date"):
        embed.set_footer(text=f"Grounded in daily board for {payload['board_date']}")
    return [embed]


def build_board_embed(payload: dict) -> discord.Embed:
    board_type = str(payload.get("board_type") or "daily").title()
    coverage_label = payload.get("coverage_label") or payload.get("generated_for_date") or "unknown window"
    embed = discord.Embed(
        title=f"{board_type} Board - {coverage_label}",
        description=(payload.get("story") or payload.get("summary") or "Board generated from validated signals.")[:4000],
        color=discord.Color.blue() if payload.get("board_type") == "daily" else discord.Color.dark_blue(),
    )
    if payload.get("what_mattered"):
        embed.add_field(
            name="What Mattered",
            value=_format_digest_entries(payload["what_mattered"], limit=8, max_line=180),
            inline=False,
        )
    if payload.get("carry_forward"):
        embed.add_field(
            name="Carry Forward",
            value=_format_digest_entries(payload["carry_forward"], limit=8, max_line=180),
            inline=False,
        )
    if payload.get("project_signals"):
        embed.add_field(
            name="Project Signals",
            value=_format_digest_entries(
                [f"{item.get('project')}: {item.get('summary')}" for item in payload["project_signals"][:6]],
                limit=6,
                max_line=180,
            ),
            inline=False,
        )
    if payload.get("source_count") is not None:
        embed.set_footer(
            text=(
                f"Validated sources: {payload.get('source_count', 0)}"
                f" | Excluded: {payload.get('excluded_count', 0)}"
            )
        )
    return embed


async def setup(bot: commands.Bot):
    await bot.add_cog(InboxCog(bot))
