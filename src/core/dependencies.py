# src/core/dependencies.py

import hmac
import hashlib
from fastapi import Request, HTTPException
import httpx

from src.core.config import SESSION_SECRET, MANAGE_GUILD, ADMINISTRATOR
from src.core.db import WebSession, get_session_by_sid
from src.services.discord import fetch_user_guilds


def sign_value(value: str) -> str:
    """Sign a value with HMAC-SHA256."""
    sig = hmac.new(SESSION_SECRET.encode(), value.encode(), hashlib.sha256).hexdigest()
    return f"{value}.{sig}"


def verify_signed_value(signed: str | None) -> str | None:
    """Verify and extract a signed value."""
    if not signed or "." not in signed:
        return None
    value, sig = signed.rsplit(".", 1)
    expected = hmac.new(SESSION_SECRET.encode(), value.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    return value


def get_http_client(request: Request) -> httpx.AsyncClient:
    """Get the shared HTTP client from app state."""
    return request.app.state.http_client


async def get_current_session(request: Request) -> WebSession:
    """Get current session from cookie or raise 401."""
    sid = verify_signed_value(request.cookies.get("sid"))
    if not sid:
        raise HTTPException(status_code=401, detail="Not logged in")

    sess = await get_session_by_sid(sid)
    if not sess:
        raise HTTPException(status_code=401, detail="Not logged in")

    return sess


async def require_manage_guild_permission(
    request: Request,
    sess: WebSession,
    guild_id: int,
) -> None:
    """Check if user has manage_guild permission for the target guild."""
    client = get_http_client(request)
    user_guilds = await fetch_user_guilds(client, sess.access_token)

    target = next((g for g in user_guilds if str(g.get("id")) == str(guild_id)), None)
    if not target:
        raise HTTPException(status_code=403, detail="Missing guild access")

    perms = int(target.get("permissions", 0))
    is_owner = target.get("owner", False)
    if not is_owner and (perms & MANAGE_GUILD) != MANAGE_GUILD and (perms & ADMINISTRATOR) != ADMINISTRATOR:
        raise HTTPException(status_code=403, detail="Missing manage_guild permission")
