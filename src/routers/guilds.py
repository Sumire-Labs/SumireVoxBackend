# src/routers/guilds.py

import logging
from fastapi import APIRouter, Request, HTTPException

from src.core.config import DEFAULT_SETTINGS, MANAGE_GUILD, ADMINISTRATOR
from src.core.database import (
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
from src.services.discord import fetch_user_guilds, is_bot_in_guild

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/guilds", tags=["guilds"])


@router.get("")
async def get_guilds(request: Request):
    """Get all manageable guilds for the current user."""
    sess = await get_current_session(request)
    client = get_http_client(request)

    user_guilds = await fetch_user_guilds(client, sess.access_token)

    manageable_guilds = []
    for g in user_guilds:
        is_manageable = g.get("owner", False) or \
                        (int(g["permissions"]) & MANAGE_GUILD) == MANAGE_GUILD or \
                        (int(g["permissions"]) & ADMINISTRATOR) == ADMINISTRATOR

        if is_manageable:
            guild_id = int(g["id"])
            bot_in_guild = await is_bot_in_guild(client, guild_id)

            manageable_guilds.append({
                "id": g["id"],
                "name": g["name"],
                "icon": g["icon"],
                "permissions": g["permissions"],
                "bot_in_guild": bot_in_guild
            })

    return manageable_guilds


@router.get("/{guild_id}/settings")
async def get_settings(guild_id: int, request: Request):
    """Get guild settings."""
    sess = await get_current_session(request)
    await require_manage_guild_permission(request, sess, guild_id)

    settings = await get_guild_settings(guild_id)
    if not settings:
        client = get_http_client(request)
        if await is_bot_in_guild(client, guild_id):
            return DEFAULT_SETTINGS
        else:
            return {}
    return settings


@router.patch("/{guild_id}/settings")
async def update_settings_endpoint(guild_id: int, request: Request):
    """Update guild settings."""
    sess = await get_current_session(request)
    await require_manage_guild_permission(request, sess, guild_id)

    new_settings = await request.json()

    # Premium check
    boost_count = await get_guild_boost_count(guild_id)
    if boost_count < 1:
        if new_settings.get("max_chars", 0) > 50:
            new_settings["max_chars"] = 50
        new_settings["auto_join"] = False
    else:
        if new_settings.get("max_chars", 0) > 200:
            new_settings["max_chars"] = 200

    await update_guild_settings(guild_id, new_settings)
    return {"ok": True}


@router.get("/{guild_id}/dict")
async def get_dict(guild_id: int, request: Request):
    """Get guild dictionary."""
    sess = await get_current_session(request)
    await require_manage_guild_permission(request, sess, guild_id)

    d = await get_guild_dict(guild_id)
    return [{"word": k, "reading": v} for k, v in d.items()]


@router.post("/{guild_id}/dict")
async def add_dict(guild_id: int, request: Request):
    """Add word to guild dictionary."""
    sess = await get_current_session(request)
    await require_manage_guild_permission(request, sess, guild_id)

    payload = await request.json()
    word = payload.get("word")
    reading = payload.get("reading")

    if not word or not reading:
        raise HTTPException(status_code=400, detail="word and reading are required")

    d = await get_guild_dict(guild_id)

    # Premium check
    boost_count = await get_guild_boost_count(guild_id)
    limit = 100 if boost_count >= 1 else 10

    if len(d) >= limit and word not in d:
        raise HTTPException(
            status_code=403,
            detail=f"Dictionary limit reached ({limit}). Upgrade to premium for more slots."
        )

    d[word] = reading
    await update_guild_dict(guild_id, d)
    return {"ok": True}


@router.delete("/{guild_id}/dict/{word}")
async def delete_dict(guild_id: int, word: str, request: Request):
    """Delete word from guild dictionary."""
    sess = await get_current_session(request)
    await require_manage_guild_permission(request, sess, guild_id)

    d = await get_guild_dict(guild_id)
    if word in d:
        del d[word]
        await update_guild_dict(guild_id, d)
    return {"ok": True}
