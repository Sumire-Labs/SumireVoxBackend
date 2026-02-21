# src/routers/billing.py

import logging
import stripe
from fastapi import APIRouter, Request, HTTPException

from src.core.config import MANAGE_GUILD, ADMINISTRATOR
from src.core.database import (
    get_user_billing,
    create_or_update_user,
    get_guild_boost_count,
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
    is_bot_in_guild,
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


@router.get("/status")
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

    manageable_guilds = []
    for g in user_guilds:
        guild_id = int(g["id"])
        boost_count = await get_guild_boost_count(guild_id)
        bot_in_guild = await is_bot_in_guild(client, guild_id)

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
async def get_billing_config():
    """Get billing configuration."""
    instances = await get_bot_instances_cached()

    client_id_0 = instances[0]["client_id"] if instances else None

    return {
        "bot_instances": instances,
        "client_id_0": client_id_0,
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
async def create_checkout_session_endpoint(request: Request):
    """Create a Stripe checkout session."""
    sess = await get_current_session(request)

    try:
        await create_or_update_user(sess.discord_user_id)

        user_billing = await get_user_billing(sess.discord_user_id)
        customer_id = user_billing.get("stripe_customer_id") if user_billing else None

        url = await create_checkout_session(sess.discord_user_id, customer_id)
        return {"url": url}
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error during checkout session creation: {e}")
        raise HTTPException(status_code=500, detail="Payment service error")
    except Exception as e:
        logger.error(f"Unexpected error during checkout session creation: {e}")
        raise HTTPException(status_code=500, detail="An unexpected error occurred")


@router.post("/boost")
async def boost_guild(request: Request):
    """Boost a guild."""
    sess = await get_current_session(request)
    payload = await request.json()
    guild_id = payload.get("guild_id")

    if not guild_id:
        raise HTTPException(status_code=400, detail="guild_id is required")

    client = get_http_client(request)
    bot_in_guild = await is_bot_in_guild(client, int(guild_id))

    if not bot_in_guild:
        await require_manage_guild_permission(request, sess, int(guild_id))

    max_boosts = await get_max_boosts_per_guild()

    boost_count = await get_guild_boost_count(int(guild_id))
    if boost_count >= max_boosts:
        raise HTTPException(status_code=400, detail=f"Guild reached max boost limit ({max_boosts})")

    status = await get_user_billing(sess.discord_user_id)
    if not status or status["total_slots"] <= len(status["boosts"]):
        raise HTTPException(status_code=400, detail="No available slots")

    success = await activate_guild_boost(int(guild_id), sess.discord_user_id, max_boosts=max_boosts)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to activate boost")

    return {"ok": True}


@router.post("/unboost")
async def unboost_guild(request: Request):
    """Remove boost from a guild."""
    sess = await get_current_session(request)
    payload = await request.json()
    guild_id = payload.get("guild_id")

    if not guild_id:
        raise HTTPException(status_code=400, detail="guild_id is required")

    try:
        success = await deactivate_guild_boost(int(guild_id), sess.discord_user_id)
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
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events."""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = verify_webhook_signature(payload, sig_header)
    except ValueError:
        logger.error("Webhook error: Invalid payload")
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError as e:
        logger.error(f"Webhook error: Invalid signature ({e})")
        raise HTTPException(status_code=400, detail="Invalid signature")

    try:
        return await process_webhook_event(event)
    except Exception as e:
        logger.error(f"Error processing webhook event {event['id']}: {e}")
        raise HTTPException(status_code=500, detail="Webhook processing error")
