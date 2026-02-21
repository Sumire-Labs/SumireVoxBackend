# src/services/discord.py

import logging
from datetime import datetime, timezone
from typing import List

import httpx
from fastapi import HTTPException
from cachetools import TTLCache

from src.core.config import (
    DISCORD_BOT_TOKEN,
    GUILDS_CACHE_TTL,
    BOT_GUILDS_CACHE_TTL,
    BOT_INSTANCES_CACHE_TTL,
)
from src.core.database import get_bot_instances

logger = logging.getLogger(__name__)

# Caches
GUILDS_CACHE: TTLCache = TTLCache(maxsize=200, ttl=GUILDS_CACHE_TTL)

# Bot guilds cache
_bot_guilds_cache: List[str] | None = None
_bot_guilds_cache_ts: datetime | None = None

# Bot instances cache
_bot_instances_cache: List[dict] | None = None
_bot_instances_cache_ts: datetime | None = None


async def fetch_user_guilds(client: httpx.AsyncClient, access_token: str) -> list:
    """Fetch guilds from Discord or cache."""
    if access_token in GUILDS_CACHE:
        return GUILDS_CACHE[access_token]

    res = await client.get(
        "https://discord.com/api/users/@me/guilds",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if res.status_code != 200:
        raise HTTPException(
            status_code=res.status_code,
            detail="Failed to fetch guilds from Discord"
        )

    guilds = res.json()
    minimal_guilds = [
        {
            "id": g.get("id"),
            "name": g.get("name"),
            "icon": g.get("icon"),
            "permissions": g.get("permissions"),
            "owner": g.get("owner")
        }
        for g in guilds
    ]
    GUILDS_CACHE[access_token] = minimal_guilds
    return minimal_guilds


async def fetch_bot_guilds(client: httpx.AsyncClient) -> List[str]:
    """Fetch guilds where the bot is present."""
    global _bot_guilds_cache, _bot_guilds_cache_ts

    if not DISCORD_BOT_TOKEN:
        return []

    now = datetime.now(timezone.utc)
    if _bot_guilds_cache is not None and _bot_guilds_cache_ts:
        if (now - _bot_guilds_cache_ts).total_seconds() < BOT_GUILDS_CACHE_TTL:
            return _bot_guilds_cache

    res = await client.get(
        "https://discord.com/api/users/@me/guilds",
        headers={"Authorization": f"Bot {DISCORD_BOT_TOKEN}"},
    )
    if res.status_code != 200:
        if _bot_guilds_cache is not None:
            return _bot_guilds_cache
        return []

    guilds = res.json()
    _bot_guilds_cache = [g["id"] for g in guilds]
    _bot_guilds_cache_ts = now
    return _bot_guilds_cache


async def is_bot_in_guild(client: httpx.AsyncClient, guild_id: int) -> bool:
    """Check if bot is in the specified guild."""
    bot_guild_ids = await fetch_bot_guilds(client)
    return str(guild_id) in bot_guild_ids


async def get_bot_instances_cached() -> List[dict]:
    """Get bot instances from database with caching."""
    global _bot_instances_cache, _bot_instances_cache_ts

    now = datetime.now(timezone.utc)
    if _bot_instances_cache is not None and _bot_instances_cache_ts:
        if (now - _bot_instances_cache_ts).total_seconds() < BOT_INSTANCES_CACHE_TTL:
            return _bot_instances_cache

    instances = await get_bot_instances()
    _bot_instances_cache = instances
    _bot_instances_cache_ts = now

    return instances


async def get_primary_bot_client_id() -> str | None:
    """Get the primary bot's client_id (first active instance)."""
    instances = await get_bot_instances_cached()
    if instances:
        return instances[0]["client_id"]
    return None


async def get_max_boosts_per_guild() -> int:
    """Get maximum boosts per guild based on number of active bot instances."""
    instances = await get_bot_instances_cached()
    return len(instances) if instances else 1


def clear_bot_guilds_cache() -> None:
    """Clear bot guilds cache."""
    global _bot_guilds_cache, _bot_guilds_cache_ts
    _bot_guilds_cache = None
    _bot_guilds_cache_ts = None
    logger.info("BOT_GUILDS_CACHE cleared.")


def clear_bot_instances_cache() -> None:
    """Clear bot instances cache."""
    global _bot_instances_cache, _bot_instances_cache_ts
    _bot_instances_cache = None
    _bot_instances_cache_ts = None
    logger.info("BOT_INSTANCES_CACHE cleared.")


def get_cache_stats() -> dict:
    """Get cache statistics for monitoring."""
    return {
        "guilds_cache_size": len(GUILDS_CACHE),
        "bot_guilds_cache_size": len(_bot_guilds_cache) if _bot_guilds_cache else 0,
        "bot_instances_cache_size": len(_bot_instances_cache) if _bot_instances_cache else 0,
    }
