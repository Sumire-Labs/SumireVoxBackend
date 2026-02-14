import os
import secrets
import hmac
import hashlib
from urllib.parse import urlencode
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager

from dotenv import load_dotenv

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

import src.core.database as db

load_dotenv()

DISCORD_CLIENT_ID = os.environ["DISCORD_CLIENT_ID"]
DISCORD_CLIENT_SECRET = os.environ["DISCORD_CLIENT_SECRET"]
DISCORD_REDIRECT_URI = os.environ["DISCORD_REDIRECT_URI"]  # 例: https://api.sumirevox.com/auth/discord/callback
SESSION_SECRET = os.environ["SESSION_SECRET"]
DATABASE_URL = os.environ["DATABASE_URL"]
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "true").lower() == "true"

FRONTEND_AFTER_LOGIN_URL = os.environ.get("FRONTEND_AFTER_LOGIN_URL", "https://sumirevox.com/")
SESSION_TTL_DAYS = int(os.environ.get("SESSION_TTL_DAYS", "7"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db(DATABASE_URL)
    try:
        yield
    finally:
        await db.close_db()


app = FastAPI(lifespan=lifespan)


def _sign(value: str) -> str:
    sig = hmac.new(SESSION_SECRET.encode(), value.encode(), hashlib.sha256).hexdigest()
    return f"{value}.{sig}"


def _verify_signed(signed: str | None) -> str | None:
    if not signed or "." not in signed:
        return None
    value, sig = signed.split(".", 1)
    expected = hmac.new(SESSION_SECRET.encode(), value.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    return value


@app.get("/auth/discord/start")
async def discord_start():
    state = secrets.token_urlsafe(32)

    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify guilds",
        "state": state,
    }

    authorize_url = f"https://discord.com/oauth2/authorize?{urlencode(params)}"
    res = RedirectResponse(authorize_url, status_code=302)

    # state を HttpOnly Cookie で保持（CSRF対策）
    res.set_cookie(
        key="discord_oauth_state",
        value=_sign(state),
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        path="/",
        max_age=60 * 10,
    )
    return res


@app.get("/auth/discord/callback")
async def discord_callback(request: Request):
    error = request.query_params.get("error")
    if error:
        raise HTTPException(status_code=400, detail=f"Discord error: {error}")

    code = request.query_params.get("code")
    state = request.query_params.get("state")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code/state")

    state_cookie = _verify_signed(request.cookies.get("discord_oauth_state"))
    if not state_cookie or state_cookie != state:
        raise HTTPException(status_code=400, detail="Invalid state")

    # code -> token
    async with httpx.AsyncClient(timeout=20) as client:
        token_res = await client.post(
            "https://discord.com/api/oauth2/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "client_id": DISCORD_CLIENT_ID,
                "client_secret": DISCORD_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": DISCORD_REDIRECT_URI,
            },
        )
    if token_res.status_code != 200:
        raise HTTPException(status_code=401, detail=token_res.text)

    token = token_res.json()
    access_token = token.get("access_token")
    token_type = token.get("token_type", "Bearer")
    if not access_token:
        raise HTTPException(status_code=401, detail="Missing access_token")

    # token -> user
    async with httpx.AsyncClient(timeout=20) as client:
        me_res = await client.get(
            "https://discord.com/api/users/@me",
            headers={"Authorization": f"{token_type} {access_token}"},
        )
    if me_res.status_code != 200:
        raise HTTPException(status_code=401, detail="Fetch /users/@me failed")

    me = me_res.json()

    # セッション発行（DB保存）
    sid = secrets.token_hex(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)

    await db.create_session(
        sid=sid,
        discord_user_id=str(me["id"]),
        username=me.get("username"),
        expires_at=expires_at,
    )

    res = RedirectResponse(FRONTEND_AFTER_LOGIN_URL, status_code=302)

    # OAuth state cookie は消す
    res.delete_cookie("discord_oauth_state", path="/")

    # ログインセッション cookie
    res.set_cookie(
        key="sid",
        value=_sign(sid),
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        path="/",
        max_age=60 * 60 * 24 * SESSION_TTL_DAYS,
    )
    return res


@app.get("/api/me")
async def me(request: Request):
    sid = _verify_signed(request.cookies.get("sid"))
    if not sid:
        raise HTTPException(status_code=401, detail="Not logged in")

    sess = await db.get_session_by_sid(sid)
    if not sess:
        raise HTTPException(status_code=401, detail="Not logged in")

    return {"user": {"discordId": sess.discord_user_id, "username": sess.username}}


@app.post("/api/logout")
async def logout(request: Request):
    sid = _verify_signed(request.cookies.get("sid"))
    res = Response(status_code=204)

    res.delete_cookie("sid", path="/")
    if sid:
        await db.delete_session(sid)

    return res