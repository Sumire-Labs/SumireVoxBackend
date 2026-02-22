# src/routers/guilds.py

import logging
from fastapi import APIRouter, Request, HTTPException
from pydantic import ValidationError
from slowapi import Limiter
from slowapi.util import get_remote_address

from src.core.config import (
    DEFAULT_SETTINGS,
    MANAGE_GUILD,
    ADMINISTRATOR,
    FREE_MAX_CHARS,
    PREMIUM_MAX_CHARS,
    FREE_DICT_LIMIT,
    PREMIUM_DICT_LIMIT,
)
from src.core.models import GuildSettingsUpdate, DictEntry
from src.core.db import (
    get_guild_settings,
    update_guild_settings,
    get_guild_dict,
    update_guild_dict,
    get_guild_boost_count,
)
from src.core.dependencies import (
    get_http_client,
    get_current_session,
    require_manage_guild_permission,
)
from src.services.discord import fetch_user_guilds, fetch_bot_guilds_as_set

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/guilds", tags=["guilds"])

limiter = Limiter(key_func=get_remote_address)


@router.get("")
@limiter.limit("30/minute")
async def get_guilds(request: Request):
    """Get all manageable guilds for the current user."""
    sess = await get_current_session(request)
    client = get_http_client(request)

    user_guilds = await fetch_user_guilds(client, sess.access_token)
    bot_guild_set = await fetch_bot_guilds_as_set(client)

    manageable_guilds = []
    for g in user_guilds:
        is_manageable = g.get("owner", False) or \
                        (int(g["permissions"]) & MANAGE_GUILD) == MANAGE_GUILD or \
                        (int(g["permissions"]) & ADMINISTRATOR) == ADMINISTRATOR

        if is_manageable:
            guild_id = g["id"]
            bot_in_guild = guild_id in bot_guild_set

            manageable_guilds.append({
                "id": guild_id,
                "name": g["name"],
                "icon": g["icon"],
                "permissions": g["permissions"],
                "bot_in_guild": bot_in_guild
            })

    return manageable_guilds


@router.get("/{guild_id}/settings")
@limiter.limit("60/minute")
async def get_settings(guild_id: int, request: Request):
    """Get guild settings."""
    sess = await get_current_session(request)
    await require_manage_guild_permission(request, sess, guild_id)

    settings = await get_guild_settings(guild_id)
    if not settings:
        client = get_http_client(request)
        bot_guild_set = await fetch_bot_guilds_as_set(client)
        if str(guild_id) in bot_guild_set:
            return DEFAULT_SETTINGS
        else:
            return {}
    return settings


@router.patch("/{guild_id}/settings")
@limiter.limit("30/minute")
async def update_settings_endpoint(guild_id: int, request: Request):
    """Update guild settings."""
    sess = await get_current_session(request)
    await require_manage_guild_permission(request, sess, guild_id)

    try:
        raw_data = await request.json()
        settings_update = GuildSettingsUpdate(**raw_data)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors())
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request")

    current_settings = await get_guild_settings(guild_id)
    if not current_settings:
        current_settings = DEFAULT_SETTINGS.copy()

    new_settings = {**current_settings, **settings_update.to_update_dict()}

    boost_count = await get_guild_boost_count(guild_id)
    if boost_count < 1:
        if new_settings.get("max_chars", 0) > FREE_MAX_CHARS:
            new_settings["max_chars"] = FREE_MAX_CHARS
        new_settings["auto_join"] = False
    else:
        if new_settings.get("max_chars", 0) > PREMIUM_MAX_CHARS:
            new_settings["max_chars"] = PREMIUM_MAX_CHARS

    await update_guild_settings(guild_id, new_settings)
    logger.info(f"Settings updated for guild {guild_id} by user {sess.discord_user_id}")
    return {"ok": True}


@router.get("/{guild_id}/dict")
@limiter.limit("60/minute")
async def get_dict(guild_id: int, request: Request):
    """Get guild dictionary."""
    sess = await get_current_session(request)
    await require_manage_guild_permission(request, sess, guild_id)

    d = await get_guild_dict(guild_id)
    return [{"word": k, "reading": v} for k, v in d.items()]


@router.post("/{guild_id}/dict")
@limiter.limit("30/minute")
async def add_dict(guild_id: int, request: Request):
    """Add word to guild dictionary."""
    sess = await get_current_session(request)
    await require_manage_guild_permission(request, sess, guild_id)

    try:
        raw_data = await request.json()
        entry = DictEntry(**raw_data)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors())
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request")

    d = await get_guild_dict(guild_id)

    boost_count = await get_guild_boost_count(guild_id)
    limit = PREMIUM_DICT_LIMIT if boost_count >= 1 else FREE_DICT_LIMIT

    if len(d) >= limit and entry.word not in d:
        raise HTTPException(
            status_code=403,
            detail=f"Dictionary limit reached ({limit}). Upgrade to premium for more slots."
        )

    d[entry.word] = entry.reading
    await update_guild_dict(guild_id, d)
    logger.info(f"Dictionary updated for guild {guild_id}: added '{entry.word}'")
    return {"ok": True}


@router.delete("/{guild_id}/dict/{word}")
@limiter.limit("30/minute")
async def delete_dict(guild_id: int, word: str, request: Request):
    """Delete word from guild dictionary."""
    sess = await get_current_session(request)
    await require_manage_guild_permission(request, sess, guild_id)

    d = await get_guild_dict(guild_id)
    if word in d:
        del d[word]
        await update_guild_dict(guild_id, d)
        logger.info(f"Dictionary updated for guild {guild_id}: deleted '{word}'")
    return {"ok": True}
