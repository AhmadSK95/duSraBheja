"""Redis-backed lightweight notifications for bot-visible events."""

from __future__ import annotations

import json

from redis.asyncio import Redis

from src.config import settings


async def publish(channel: str, payload: dict) -> None:
    redis = Redis.from_url(settings.redis_url)
    try:
        await redis.publish(channel, json.dumps(payload))
    finally:
        await redis.aclose()
