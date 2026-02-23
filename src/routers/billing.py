# src/routers/billing.py

import logging
import stripe
from fastapi import APIRouter, Request, HTTPException
from pydantic import ValidationError
from slowapi import Limiter
from slowapi.util import get_remote_address

from src.core.config import MANAGE_GUILD, ADMINISTRATOR
from src.core.models import BoostRequest
from src.core.db import (
    get_user_billing,
    create_or_update_user,
    get_guild_boost_count,
    get_guild_boost_counts_batch,
    activate_guild_boost,
    deactivate_guild_boost,
)
from src.core.dependencies import (
    get_http_client,
    get_current_session,
    require_manage_guild_permission,
)
from src.services.discord import (
    fetch_user_guilds,
    fetch_bot_guilds_as_set,
    get_bot_instances_cached,
    get_max_boosts_per_guild,
)
from src.services.stripe_service import (
    create_checkout_session,
    verify_webhook_signature,
    process_webhook_event,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/billing", tags=["billing"])

limiter = Limiter(key_func=get_remote_address)


@router.get("/status")
@limiter.limit("30/minute")
async def get_billing_status(request: Request):
    """Get billing status for current user."""
    sess = await get_current_session(request)

    status = await get_user_billing(sess.discord_user_id)
    if not status:
        status = {
            "total_slots": 0,
            "used_slots": 0,
            "boosts": []
        }

    client = get_http_client(request)
    user_guilds = await fetch_user_guilds(client, sess.access_token)
    guild_map = {str(g["id"]): g["name"] for g in user_guilds}

    boosts_with_names = []
    for b in status.get("boosts", []):
        guild_id_str = str(b["guild_id"])
        boosts_with_names.append({
            "guild_id": guild_id_str,
            "guild_name": guild_map.get(guild_id_str, "Unknown Server")
        })

    instances = await get_bot_instances_cached()
    bot_guild_set = await fetch_bot_guilds_as_set(client)

    guild_ids_to_check = []
    for g in user_guilds:
        guild_id = int(g["id"])
        if str(guild_id) in bot_guild_set:
            guild_ids_to_check.append(guild_id)

    boost_counts = await get_guild_boost_counts_batch(guild_ids_to_check)

    boost_guild_ids = [int(b["guild_id"]) for b in status.get("boosts", [])]
    additional_guild_ids = [gid for gid in boost_guild_ids if gid not in guild_ids_to_check]
    if additional_guild_ids:
        additional_counts = await get_guild_boost_counts_batch(additional_guild_ids)
        boost_counts.update(additional_counts)

    manageable_guilds = []
    for g in user_guilds:
        guild_id = int(g["id"])
        guild_id_str = str(guild_id)
        bot_in_guild = guild_id_str in bot_guild_set
        boost_count = boost_counts.get(guild_id, 0)

        if bot_in_guild or boost_count > 0:
            is_manageable = g.get("owner", False) or \
                            (int(g["permissions"]) & MANAGE_GUILD) == MANAGE_GUILD or \
                            (int(g["permissions"]) & ADMINISTRATOR) == ADMINISTRATOR

            benefits = []
            if boost_count >= 1:
                benefits.append("Premium Features")

            for i, inst in enumerate(instances):
                if i == 0:
                    continue
                if boost_count >= i + 1:
                    benefits.append(f"{inst['bot_name']} Unlocked")

            manageable_guilds.append({
                "id": g["id"],
                "name": g["name"],
                "icon": g["icon"],
                "boost_count": boost_count,
                "bot_in_guild": bot_in_guild,
                "benefits": benefits,
                "is_manageable": is_manageable
            })

    return {
        "total_slots": status.get("total_slots", 0),
        "used_slots": status.get("used_slots") if "used_slots" in status else len(status.get("boosts", [])),
        "boosts": boosts_with_names,
        "manageable_guilds": manageable_guilds
    }


@router.get("/config")
@limiter.limit("30/minute")
async def get_billing_config(request: Request):
    """Get billing configuration."""
    instances = await get_bot_instances_cached()

    return {
        "bot_instances": [
            {
                "id": i["id"],
                "bot_name": i["bot_name"],
                "client_id": i["client_id"]
            }
            for i in instances
        ],
        "max_boosts_per_guild": len(instances)
    }


@router.get("/create-checkout-session")
async def create_checkout_session_get():
    """Block GET requests for checkout session."""
    raise HTTPException(
        status_code=405,
        detail="Checkout session creation requires a POST request."
    )


@router.post("/create-checkout-session")
@limiter.limit("5/minute")  # 決済は厳しく制限
async def create_checkout_session_endpoint(request: Request):
    """Create a Stripe checkout session."""
    sess = await get_current_session(request)

    try:
        await create_or_update_user(sess.discord_user_id)

        user_billing = await get_user_billing(sess.discord_user_id)
        customer_id = user_billing.get("stripe_customer_id") if user_billing else None

        url = await create_checkout_session(sess.discord_user_id, customer_id)
        return {"url": url}
    except stripe.StripeError as e:
        logger.error(f"Stripe error during checkout session creation: {e}")
        raise HTTPException(status_code=500, detail="Payment service error")
    except Exception as e:
        logger.error(f"Unexpected error during checkout session creation: {e}")
        raise HTTPException(status_code=500, detail="An unexpected error occurred")


@router.post("/boost")
@limiter.limit("10/minute")
async def boost_guild(request: Request):
    """Boost a guild."""
    sess = await get_current_session(request)

    try:
        raw_data = await request.json()
        boost_req = BoostRequest(**raw_data)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors())
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request")

    guild_id = boost_req.guild_id_int
    client = get_http_client(request)
    bot_guild_set = await fetch_bot_guilds_as_set(client)
    bot_in_guild = str(guild_id) in bot_guild_set

    user_guilds = await fetch_user_guilds(client, sess.access_token)
    target_guild = next((g for g in user_guilds if str(g.get("id")) == str(guild_id)), None)
    if not target_guild:
        raise HTTPException(status_code=403, detail="You must be a member of the guild to boost it")

    if not bot_in_guild:
        raise HTTPException(
            status_code=400,
            detail="Bot must be in the guild before boosting"
        )

    max_boosts = await get_max_boosts_per_guild()

    boost_count = await get_guild_boost_count(guild_id)
    if boost_count >= max_boosts:
        raise HTTPException(status_code=400, detail=f"Guild reached max boost limit ({max_boosts})")

    status = await get_user_billing(sess.discord_user_id)
    if not status or status["total_slots"] <= len(status["boosts"]):
        raise HTTPException(status_code=400, detail="No available slots")

    success = await activate_guild_boost(guild_id, sess.discord_user_id, max_boosts=max_boosts)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to activate boost")

    logger.info(f"User {sess.discord_user_id} boosted guild {guild_id}")
    return {"ok": True}


@router.post("/unboost")
@limiter.limit("10/minute")
async def unboost_guild(request: Request):
    """Remove boost from a guild."""
    sess = await get_current_session(request)

    try:
        raw_data = await request.json()
        boost_req = BoostRequest(**raw_data)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors())
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request")

    guild_id = boost_req.guild_id_int
    client = get_http_client(request)

    user_guilds = await fetch_user_guilds(client, sess.access_token)
    target_guild = next((g for g in user_guilds if str(g.get("id")) == str(guild_id)), None)
    if not target_guild:
        raise HTTPException(status_code=403, detail="You must be a member of the guild to boost it")

    try:
        success = await deactivate_guild_boost(guild_id, sess.discord_user_id)
        if not success:
            logger.warning(f"Unboost failed: No boost found for user {sess.discord_user_id} in guild {guild_id}")
            raise HTTPException(status_code=404, detail="Boost not found or not owned by you")

        logger.info(f"User {sess.discord_user_id} successfully unboosted guild {guild_id}")

        status = await get_user_billing(sess.discord_user_id)
        return {
            "ok": True,
            "total_slots": status["total_slots"] if status else 0,
            "used_slots": len(status["boosts"]) if status else 0
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error during unboost: {e}")
        raise HTTPException(status_code=500, detail="An unexpected error occurred")


@router.post("/webhook")
@limiter.limit("100/minute")  # Webhookは適度に制限
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events."""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    if not sig_header:
        raise HTTPException(status_code=400, detail="Missing signature")

    try:
        event = verify_webhook_signature(payload, sig_header)
    except ValueError:
        logger.error("Webhook error: Invalid payload")
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.SignatureVerificationError as e:
        logger.error(f"Webhook error: Invalid signature")
        raise HTTPException(status_code=400, detail="Invalid signature")

    try:
        return await process_webhook_event(event)
    except Exception as e:
        logger.error(f"Error processing webhook event {event['id']}: {e}")
        raise HTTPException(status_code=500, detail="Webhook processing error")
