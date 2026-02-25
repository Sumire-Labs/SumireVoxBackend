# src/routers/auth.py

import secrets
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Request, HTTPException, Response
from fastapi.responses import RedirectResponse
from slowapi import Limiter
from slowapi.util import get_remote_address

from src.core.config import (
    DISCORD_CLIENT_SECRET,
    DISCORD_REDIRECT_URI,
    FRONTEND_AFTER_LOGIN_URL,
    SESSION_TTL_DAYS,
    COOKIE_SECURE,
    IS_PRODUCTION,
)
from src.core.db import create_session, delete_session, delete_user_sessions
from src.core.dependencies import (
    sign_value,
    verify_signed_value,
    get_http_client,
    get_current_session,
)
from src.services.discord import get_primary_bot_client_id

COOKIE_DOMAIN = ".sumirevox.com"

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# ルーター用のレート制限
limiter = Limiter(key_func=get_remote_address)


@router.get("/discord/start")
@limiter.limit("10/minute")  # 認証開始は厳しく制限
async def discord_start(request: Request):
    """Start Discord OAuth2 flow."""
    state = secrets.token_urlsafe(32)

    client_id = await get_primary_bot_client_id()
    if not client_id:
        raise HTTPException(status_code=500, detail="Service temporarily unavailable")

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
@limiter.limit("10/minute")  # コールバックも厳しく制限
async def discord_callback(request: Request):
    """Handle Discord OAuth2 callback."""
    error = request.query_params.get("error")
    if error:
        logger.warning(f"Discord OAuth error: {error}")
        raise HTTPException(status_code=400, detail="Authentication failed")

    code = request.query_params.get("code")
    state = request.query_params.get("state")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Invalid request")

    state_cookie = verify_signed_value(request.cookies.get("discord_oauth_state"))
    if not state_cookie or not secrets.compare_digest(state_cookie, state):
        raise HTTPException(status_code=400, detail="Invalid request")

    client = get_http_client(request)

    client_id = await get_primary_bot_client_id()
    if not client_id:
        raise HTTPException(status_code=500, detail="Service temporarily unavailable")

    # Exchange code for token
    try:
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
    except Exception as e:
        logger.error(f"Error exchanging code for token: {e}")
        raise HTTPException(status_code=500, detail="Authentication failed")

    if token_res.status_code != 200:
        logger.warning(f"Token exchange failed: {token_res.status_code}")
        raise HTTPException(status_code=401, detail="Authentication failed")

    token = token_res.json()
    access_token = token.get("access_token")
    token_type = token.get("token_type", "Bearer")
    if not access_token:
        raise HTTPException(status_code=401, detail="Authentication failed")

    # Fetch user info
    try:
        me_res = await client.get(
            "https://discord.com/api/users/@me",
            headers={"Authorization": f"{token_type} {access_token}"},
        )
    except Exception as e:
        logger.error(f"Error fetching user info: {e}")
        raise HTTPException(status_code=500, detail="Authentication failed")

    if me_res.status_code != 200:
        raise HTTPException(status_code=401, detail="Authentication failed")

    me = me_res.json()
    discord_user_id = str(me["id"])

    # 【修正】既存のセッションを削除（セッション固定攻撃対策）
    await delete_user_sessions(discord_user_id)

    # Create new session
    sid = secrets.token_hex(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)

    await create_session(
        sid=sid,
        discord_user_id=discord_user_id,
        username=me.get("username"),
        access_token=access_token,
        expires_at=expires_at,
    )

    res = RedirectResponse(FRONTEND_AFTER_LOGIN_URL, status_code=302)
    res.delete_cookie("discord_oauth_state", path="/")
    if IS_PRODUCTION:
        res.set_cookie(
            key="sid",
            value=sign_value(sid),
            httponly=True,
            secure=True,
            samesite="none",
            domain=COOKIE_DOMAIN,  # 本番のみ
            path="/",
            max_age=60 * 60 * 24 * SESSION_TTL_DAYS,
        )
    else:
        res.set_cookie(
            key="sid",
            value=sign_value(sid),
            httponly=True,
            secure=False,
            samesite="lax",  # 開発環境ではlax
            path="/",
            max_age=60 * 60 * 24 * SESSION_TTL_DAYS,
        )

    logger.info(f"User {discord_user_id} logged in successfully")
    return res


@router.get("/me")
@limiter.limit("60/minute")
async def me(request: Request):
    """Get current user info."""
    sess = await get_current_session(request)
    return {"user": {"discordId": sess.discord_user_id, "username": sess.username}}


@router.post("/logout")
@limiter.limit("30/minute")
async def logout(request: Request):
    """Logout current user."""
    sid = verify_signed_value(request.cookies.get("sid"))
    res = Response(status_code=204)

    res.delete_cookie("sid", path="/", samesite="strict", secure=COOKIE_SECURE)
    if sid:
        await delete_session(sid)
        logger.info(f"Session logged out: {sid[:8]}...")

    return res


@router.post("/logout-all")
@limiter.limit("5/minute")
async def logout_all(request: Request):
    """Logout from all devices."""
    sess = await get_current_session(request)

    await delete_user_sessions(sess.discord_user_id)

    res = Response(status_code=204)
    res.delete_cookie("sid", path="/", samesite="strict", secure=COOKIE_SECURE)

    logger.info(f"All sessions logged out for user: {sess.discord_user_id}")
    return res
