# src/services/stripe_service.py

import asyncio
import logging
import stripe

from src.core.config import STRIPE_API_KEY, STRIPE_WEBHOOK_SECRET, STRIPE_PRICE_ID, DOMAIN
from src.core.db import (
    create_or_update_user,
    add_user_slots,
    reset_user_slots_by_customer,
    handle_refund_by_customer,
    is_event_processed,
    mark_event_processed,
)

logger = logging.getLogger(__name__)

# Initialize Stripe
stripe.api_key = STRIPE_API_KEY


async def create_checkout_session(discord_user_id: str, customer_id: str | None) -> str:
    """
    Create a Stripe checkout session and return the URL.
    Uses asyncio.to_thread to avoid blocking the event loop.
    """
    def _create_session():
        return stripe.checkout.Session.create(
            customer=customer_id,
            line_items=[
                {
                    "price": STRIPE_PRICE_ID,
                    "quantity": 1,
                },
            ],
            mode="subscription",
            success_url=f"{DOMAIN}/dashboard/premium?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{DOMAIN}/dashboard/premium",
            metadata={
                "discord_id": discord_user_id
            },
            subscription_data={
                "metadata": {
                    "discord_id": discord_user_id
                }
            }
        )

    checkout_session = await asyncio.to_thread(_create_session)
    return checkout_session.url


def verify_webhook_signature(payload: bytes, sig_header: str) -> dict:
    """
    Verify Stripe webhook signature and return the event.
    Raises ValueError or stripe.SignatureVerificationError on failure.
    """
    return stripe.Webhook.construct_event(
        payload, sig_header, STRIPE_WEBHOOK_SECRET
    )


async def handle_checkout_completed(event_id: str, session_data: dict) -> bool:
    """
    Handle checkout.session.completed event.
    Returns True if processed successfully.
    """
    discord_id = session_data.get("metadata", {}).get("discord_id")
    customer_id = session_data.get("customer")

    logger.info(f"Processing checkout.session.completed: discord_id={discord_id}, customer_id={customer_id}")

    if not discord_id or not customer_id:
        logger.warning("Missing discord_id or customer_id in session metadata")
        return False

    await create_or_update_user(discord_id, customer_id)
    await add_user_slots(customer_id, 1)
    await mark_event_processed(event_id)

    logger.info(f"Successfully updated slots for user {discord_id}")
    return True


async def handle_subscription_deleted(event_id: str, subscription_data: dict) -> bool:
    """
    Handle customer.subscription.deleted event.
    Returns True if processed successfully.
    """
    customer_id = subscription_data.get("customer")

    logger.info(f"Processing customer.subscription.deleted: customer_id={customer_id}")

    if not customer_id:
        logger.warning("Missing customer_id in subscription data")
        return False

    await reset_user_slots_by_customer(customer_id)
    await mark_event_processed(event_id)

    logger.info(f"Successfully reset slots for customer {customer_id}")
    return True


async def handle_charge_refunded(event_id: str, charge_data: dict) -> bool:
    """
    Handle charge.refunded event.
    Returns True if processed successfully.
    """
    customer_id = charge_data.get("customer")

    logger.info(f"Processing charge.refunded: customer_id={customer_id}")

    if not customer_id:
        logger.warning("Missing customer_id in charge data")
        return False

    result = await handle_refund_by_customer(customer_id)

    if result:
        logger.info(
            f"Refund handled for user {result['discord_id']}: "
            f"{result['old_total']} -> {result['new_total']} slots. "
            f"Removed boosts: {result['removed_guilds']}"
        )
        await mark_event_processed(event_id)
        return True
    else:
        logger.warning(f"No user found for customer_id {customer_id} during refund")
        return False


async def process_webhook_event(event: dict) -> dict:
    """
    Process a Stripe webhook event.
    Returns a status dict.
    """
    event_id = event["id"]
    event_type = event["type"]

    # Check idempotency
    if await is_event_processed(event_id):
        logger.info(f"Event {event_id} already processed, skipping.")
        return {"status": "success", "info": "already processed"}

    logger.info(f"Stripe Webhook received: {event_type} (id: {event_id})")

    data_object = event["data"]["object"]

    if event_type == "checkout.session.completed":
        await handle_checkout_completed(event_id, data_object)
    elif event_type == "customer.subscription.deleted":
        await handle_subscription_deleted(event_id, data_object)
    elif event_type == "charge.refunded":
        await handle_charge_refunded(event_id, data_object)

    return {"status": "success"}
