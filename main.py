import os
import secrets
import hmac
import hashlib
from urllib.parse import urlencode
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager

import stripe
from dotenv import load_dotenv

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware

import src.core.database as db

load_dotenv()

DISCORD_CLIENT_ID = os.environ["DISCORD_CLIENT_ID"]
DISCORD_CLIENT_SECRET = os.environ["DISCORD_CLIENT_SECRET"]
DISCORD_REDIRECT_URI = os.environ["DISCORD_REDIRECT_URI"]  # 例: https://api.sumirevox.com/auth/discord/callback
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
SESSION_SECRET = os.environ["SESSION_SECRET"]
DATABASE_URL = os.environ["DATABASE_URL"]
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "true").lower() == "true"

STRIPE_API_KEY = os.environ.get("STRIPE_API_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
STRIPE_PRICE_ID = os.environ.get("STRIPE_PRICE_ID")
DOMAIN = os.environ.get("DOMAIN", "http://localhost:5173")

stripe.api_key = STRIPE_API_KEY

FRONTEND_AFTER_LOGIN_URL = os.environ.get("FRONTEND_AFTER_LOGIN_URL", "https://sumirevox.com/")
SESSION_TTL_DAYS = int(os.environ.get("SESSION_TTL_DAYS", "7"))

# Discord permissions
ADMINISTRATOR = 0x8
MANAGE_GUILD = 0x20

# Caching for Discord API
# token -> (timestamp, guilds_list)
GUILDS_CACHE = {}
GUILDS_CACHE_TTL = 30  # seconds

# Bot guilds cache (to check bot presence)
BOT_GUILDS_CACHE = None
BOT_GUILDS_CACHE_TS = None
BOT_GUILDS_CACHE_TTL = 60  # seconds

DEFAULT_SETTINGS = {
    "auto_join": False,
    "auto_join_config": {},
    "max_chars": 50,
    "read_vc_status": False,
    "read_mention": True,
    "read_emoji": True,
    "add_suffix": False,
    "read_romaji": False,
    "read_attachments": True,
    "skip_code_blocks": True,
    "skip_urls": True,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db(DATABASE_URL)
    app.state.http_client = httpx.AsyncClient(timeout=20)
    try:
        yield
    finally:
        await app.state.http_client.aclose()
        await db.close_db()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[DOMAIN, "http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
        access_token=access_token,
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


async def get_current_session(request: Request) -> db.WebSession:
    sid = _verify_signed(request.cookies.get("sid"))
    if not sid:
        raise HTTPException(status_code=401, detail="Not logged in")

    sess = await db.get_session_by_sid(sid)
    if not sess:
        raise HTTPException(status_code=401, detail="Not logged in")

    return sess


async def fetch_user_guilds(client: httpx.AsyncClient, access_token: str) -> list:
    """
    Fetch guilds from Discord or cache.
    """
    now = datetime.now()
    if access_token in GUILDS_CACHE:
        ts, guilds = GUILDS_CACHE[access_token]
        if (now - ts).total_seconds() < GUILDS_CACHE_TTL:
            return guilds

    res = await client.get(
        "https://discord.com/api/users/@me/guilds",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if res.status_code != 200:
        raise HTTPException(status_code=res.status_code, detail=f"Failed to fetch guilds from Discord: {res.text}")

    guilds = res.json()
    GUILDS_CACHE[access_token] = (now, guilds)
    return guilds


async def fetch_bot_guilds(client: httpx.AsyncClient) -> list:
    """
    Fetch guilds where the bot is present.
    """
    global BOT_GUILDS_CACHE, BOT_GUILDS_CACHE_TS
    if not DISCORD_BOT_TOKEN:
        return []

    now = datetime.now()
    if BOT_GUILDS_CACHE is not None and BOT_GUILDS_CACHE_TS:
        if (now - BOT_GUILDS_CACHE_TS).total_seconds() < BOT_GUILDS_CACHE_TTL:
            return BOT_GUILDS_CACHE

    # Bot as user guilds
    res = await client.get(
        "https://discord.com/api/users/@me/guilds",
        headers={"Authorization": f"Bot {DISCORD_BOT_TOKEN}"},
    )
    if res.status_code != 200:
        # If it fails, we might just return the cached version or empty list
        if BOT_GUILDS_CACHE is not None:
            return BOT_GUILDS_CACHE
        return []

    guilds = res.json()
    BOT_GUILDS_CACHE = [g["id"] for g in guilds]
    BOT_GUILDS_CACHE_TS = now
    return BOT_GUILDS_CACHE


async def is_bot_in_guild(client: httpx.AsyncClient, guild_id: int) -> bool:
    bot_guild_ids = await fetch_bot_guilds(client)
    return str(guild_id) in [str(gid) for gid in bot_guild_ids]


async def require_manage_guild_permission(
    request: Request,
    sess: db.WebSession,
    guild_id: int,
) -> None:
    """
    Discordの /users/@me/guilds に含まれる permissions を見て、
    対象guildで manage_guild(0x20) を持っている場合のみ許可する。
    """
    client: httpx.AsyncClient = request.app.state.http_client
    user_guilds = await fetch_user_guilds(client, sess.access_token)

    target = next((g for g in user_guilds if str(g.get("id")) == str(guild_id)), None)
    if not target:
        # 所属していない（または見えない）guild
        raise HTTPException(status_code=403, detail="Missing guild access")

    perms = int(target.get("permissions", 0))
    is_owner = target.get("owner", False)
    if not is_owner and (perms & MANAGE_GUILD) != MANAGE_GUILD and (perms & ADMINISTRATOR) != ADMINISTRATOR:
        raise HTTPException(status_code=403, detail="Missing manage_guild permission")


@app.get("/api/guilds")
async def get_guilds(request: Request):
    sess = await get_current_session(request)

    client: httpx.AsyncClient = request.app.state.http_client

    # ユーザーの所属ギルド取得
    user_guilds = await fetch_user_guilds(client, sess.access_token)

    # manage_guild を持つギルドのみ抽出 (MANAGE_GUILD = 0x20)
    # または ADMINISTRATOR = 0x8, またはオーナー
    manageable_guilds = [
        {
            "id": g["id"],
            "name": g["name"],
            "icon": g["icon"],
            "permissions": g["permissions"],
        }
        for g in user_guilds
        if g.get("owner", False) or 
           (int(g["permissions"]) & MANAGE_GUILD) == MANAGE_GUILD or 
           (int(g["permissions"]) & ADMINISTRATOR) == ADMINISTRATOR
    ]

    return manageable_guilds


@app.post("/api/billing/unboost")
async def unboost_guild(request: Request):
    sess = await get_current_session(request)
    payload = await request.json()
    guild_id = payload.get("guild_id")
    
    if not guild_id:
        raise HTTPException(status_code=400, detail="guild_id is required")
    
    success = await db.deactivate_guild_boost(int(guild_id), sess.discord_user_id)
    if not success:
        raise HTTPException(status_code=404, detail="Boost not found or not owned by you")
    
    return {"ok": True}


@app.get("/api/billing/status")
async def get_billing_status(request: Request):
    sess = await get_current_session(request)
    
    status = await db.get_user_billing(sess.discord_user_id)
    if not status:
        return {
            "total_slots": 0,
            "used_slots": 0,
            "boosts": []
        }
    
    # ギルド名の解決（フロントエンドでの表示用）
    client: httpx.AsyncClient = request.app.state.http_client
    user_guilds = await fetch_user_guilds(client, sess.access_token)
    guild_map = {str(g["id"]): g["name"] for g in user_guilds}
    
    boosts_with_names = []
    for b in status.get("boosts", []):
        guild_id_str = str(b["guild_id"])
        boosts_with_names.append({
            "guild_id": guild_id_str,
            "guild_name": guild_map.get(guild_id_str, "Unknown Server")
        })
    
    return {
        "total_slots": status.get("total_slots", 0),
        "used_slots": len(status.get("boosts", [])),
        "boosts": boosts_with_names
    }


@app.get("/api/guilds/{guild_id}/settings")
async def get_settings(guild_id: int, request: Request):
    sess = await get_current_session(request)
    await require_manage_guild_permission(request, sess, guild_id)

    settings = await db.get_guild_settings(guild_id)
    if not settings:
        # Check if bot is in guild
        client: httpx.AsyncClient = request.app.state.http_client
        if await is_bot_in_guild(client, guild_id):
            return DEFAULT_SETTINGS
        else:
            # Bot not in guild, return empty to trigger invite screen
            return {}
    return settings


@app.patch("/api/guilds/{guild_id}/settings")
async def update_settings(guild_id: int, request: Request):
    sess = await get_current_session(request)
    await require_manage_guild_permission(request, sess, guild_id)

    new_settings = await request.json()
    await db.update_guild_settings(guild_id, new_settings)
    return {"ok": True}


@app.get("/api/guilds/{guild_id}/dict")
async def get_dict(guild_id: int, request: Request):
    sess = await get_current_session(request)
    await require_manage_guild_permission(request, sess, guild_id)

    d = await db.get_guild_dict(guild_id)
    # フロントエンドは [{word, reading}] のリストを期待している
    return [{"word": k, "reading": v} for k, v in d.items()]


@app.post("/api/guilds/{guild_id}/dict")
async def add_dict(guild_id: int, request: Request):
    sess = await get_current_session(request)
    await require_manage_guild_permission(request, sess, guild_id)

    payload = await request.json()
    word = payload.get("word")
    reading = payload.get("reading")

    if not word or not reading:
        raise HTTPException(status_code=400, detail="word and reading are required")

    d = await db.get_guild_dict(guild_id)
    d[word] = reading
    await db.update_guild_dict(guild_id, d)
    return {"ok": True}


@app.delete("/api/guilds/{guild_id}/dict/{word}")
async def delete_dict(guild_id: int, word: str, request: Request):
    sess = await get_current_session(request)
    await require_manage_guild_permission(request, sess, guild_id)

    d = await db.get_guild_dict(guild_id)
    if word in d:
        del d[word]
        await db.update_guild_dict(guild_id, d)
    return {"ok": True}


# --- Billing (Stripe) ---

@app.get("/api/billing/create-checkout-session")
async def create_checkout_session_get():
    raise HTTPException(status_code=405, detail="Checkout session creation requires a POST request. Please use the 'Buy' button in the dashboard.")


@app.post("/api/billing/create-checkout-session")
async def create_checkout_session(request: Request):
    sess = await get_current_session(request)
    
    try:
        # ユーザーが存在するか確認、なければ作成
        await db.create_or_update_user(sess.discord_user_id)
        
        # すでに Stripe Customer ID があるか取得
        user_billing = await db.get_user_billing(sess.discord_user_id)
        customer_id = user_billing.get("stripe_customer_id") if user_billing else None

        checkout_session = stripe.checkout.Session.create(
            customer=customer_id,
            line_items=[
                {
                    "price": STRIPE_PRICE_ID,
                    "quantity": 1,
                },
            ],
            mode="subscription",
            success_url=f"{DOMAIN}/dashboard?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{DOMAIN}/dashboard",
            metadata={
                "discord_id": sess.discord_user_id
            },
            subscription_data={
                "metadata": {
                    "discord_id": sess.discord_user_id
                }
            }
        )
        return {"url": checkout_session.url}
    except Exception as e:
        import traceback
        traceback.print_exc()  # サーバーのコンソールにエラー詳細を表示
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/billing/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        print("Webhook error: Invalid payload")
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError as e:
        print(f"Webhook error: Invalid signature ({e})")
        raise HTTPException(status_code=400, detail="Invalid signature")

    # 冪等性のチェック
    event_id = event["id"]
    if await db.is_event_processed(event_id):
        print(f"Event {event_id} already processed, skipping.")
        return {"status": "success", "info": "already processed"}

    print(f"Stripe Webhook received: {event['type']} (id: {event_id})")

    # Handle the event
    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        discord_id = session.get("metadata", {}).get("discord_id")
        customer_id = session.get("customer")
        
        print(f"Processing checkout.session.completed: discord_id={discord_id}, customer_id={customer_id}")
        
        if discord_id and customer_id:
            # ユーザーとカスタマーIDを紐付け
            await db.create_or_update_user(discord_id, customer_id)
            # スロットを加算
            await db.add_user_slots(customer_id, 1)
            print(f"Successfully updated slots for user {discord_id}")
            await db.mark_event_processed(event_id)
        else:
            print("Warning: Missing discord_id or customer_id in session metadata")

    elif event["type"] == "customer.subscription.deleted":
        subscription = event["data"]["object"]
        customer_id = subscription.get("customer")
        print(f"Processing customer.subscription.deleted: customer_id={customer_id}")
        if customer_id:
            await db.reset_user_slots_by_customer(customer_id)
            print(f"Successfully reset slots for customer {customer_id}")
            await db.mark_event_processed(event_id)

    elif event["type"] == "charge.refunded":
        charge = event["data"]["object"]
        customer_id = charge.get("customer")
        print(f"Processing charge.refunded: customer_id={customer_id}")
        if customer_id:
            res = await db.handle_refund_by_customer(customer_id)
            if res:
                log_msg = f"Refund handled for user {res['discord_id']}: {res['old_total']} -> {res['new_total']} slots."
                if res["removed_guilds"]:
                    log_msg += f" Removed boosts from guilds: {', '.join(res['removed_guilds'])}"
                print(log_msg)
                await db.mark_event_processed(event_id)
            else:
                print(f"Warning: No user found for customer_id {customer_id} during refund")

    return {"status": "success"}
