# src/routers/auth.py

import secrets
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Request, HTTPException, Response
from fastapi.responses import RedirectResponse

from src.core.config import (
    DISCORD_CLIENT_SECRET,
    DISCORD_REDIRECT_URI,
    FRONTEND_AFTER_LOGIN_URL,
    SESSION_TTL_DAYS,
    COOKIE_SECURE,
)
from src.core.db import create_session, delete_session
from src.core.dependencies import (
    sign_value,
    verify_signed_value,
    get_http_client,
    get_current_session,
)
from src.services.discord import get_primary_bot_client_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/discord/start")
async def discord_start():
    """Start Discord OAuth2 flow."""
    state = secrets.token_urlsafe(32)

    client_id = await get_primary_bot_client_id()
    if not client_id:
        raise HTTPException(status_code=500, detail="No bot instance configured")

    params = {
        "client_id": client_id,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify guilds",
        "state": state,
    }

    authorize_url = f"https://discord.com/oauth2/authorize?{urlencode(params)}"
    res = RedirectResponse(authorize_url, status_code=302)

    res.set_cookie(
        key="discord_oauth_state",
        value=sign_value(state),
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        path="/",
        max_age=60 * 10,
    )
    return res


@router.get("/discord/callback")
async def discord_callback(request: Request):
    """Handle Discord OAuth2 callback."""
    error = request.query_params.get("error")
    if error:
        raise HTTPException(status_code=400, detail=f"Discord error: {error}")

    code = request.query_params.get("code")
    state = request.query_params.get("state")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code/state")

    state_cookie = verify_signed_value(request.cookies.get("discord_oauth_state"))
    if not state_cookie or state_cookie != state:
        raise HTTPException(status_code=400, detail="Invalid state")

    client = get_http_client(request)

    client_id = await get_primary_bot_client_id()
    if not client_id:
        raise HTTPException(status_code=500, detail="No bot instance configured")

    # Exchange code for token
    token_res = await client.post(
        "https://discord.com/api/oauth2/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_id": client_id,
            "client_secret": DISCORD_CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": DISCORD_REDIRECT_URI,
        },
    )
    if token_res.status_code != 200:
        raise HTTPException(status_code=401, detail="Failed to exchange code for token")

    token = token_res.json()
    access_token = token.get("access_token")
    token_type = token.get("token_type", "Bearer")
    if not access_token:
        raise HTTPException(status_code=401, detail="Missing access_token")

    # Fetch user info
    me_res = await client.get(
        "https://discord.com/api/users/@me",
        headers={"Authorization": f"{token_type} {access_token}"},
    )
    if me_res.status_code != 200:
        raise HTTPException(status_code=401, detail="Failed to fetch user info")

    me = me_res.json()

    # Create session
    sid = secrets.token_hex(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)

    await create_session(
        sid=sid,
        discord_user_id=str(me["id"]),
        username=me.get("username"),
        access_token=access_token,
        expires_at=expires_at,
    )

    res = RedirectResponse(FRONTEND_AFTER_LOGIN_URL, status_code=302)
    res.delete_cookie("discord_oauth_state", path="/")
    res.set_cookie(
        key="sid",
        value=sign_value(sid),
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        path="/",
        max_age=60 * 60 * 24 * SESSION_TTL_DAYS,
    )
    return res


@router.get("/me")
async def me(request: Request):
    """Get current user info."""
    sess = await get_current_session(request)
    return {"user": {"discordId": sess.discord_user_id, "username": sess.username}}


@router.post("/logout")
async def logout(request: Request):
    """Logout current user."""
    sid = verify_signed_value(request.cookies.get("sid"))
    res = Response(status_code=204)

    res.delete_cookie("sid", path="/")
    if sid:
        await delete_session(sid)

    return res
