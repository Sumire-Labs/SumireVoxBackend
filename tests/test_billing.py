# tests/test_billing.py

import pytest
import hmac
import hashlib
import time
import json
import os
import uuid

from fastapi.testclient import TestClient

# Set test environment before importing app
os.environ["ENV"] = "development"
os.environ["TEST_DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://user:password@localhost:5432/sumire_vox_test"
)

from main import app, STRIPE_WEBHOOK_SECRET
import src.core.database as db


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
async def setup_db():
    """Initialize and clean database before each test."""
    test_db_url = os.environ.get("TEST_DATABASE_URL")
    await db.init_db(test_db_url)

    pool = db._require_pool()
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE users, guild_boosts, web_sessions, processed_stripe_events CASCADE")

    yield

    # Cleanup after test if needed


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def generate_stripe_signature(payload: str, secret: str) -> str:
    """Generate a valid Stripe webhook signature."""
    timestamp = str(int(time.time()))
    signed_payload = f"{timestamp}.{payload}"
    signature = hmac.new(
        secret.encode(),
        signed_payload.encode(),
        hashlib.sha256
    ).hexdigest()
    return f"t={timestamp},v1={signature}"


def generate_unique_event_id() -> str:
    """Generate a unique event ID for each test."""
    return f"evt_test_{uuid.uuid4().hex[:16]}"


@pytest.mark.asyncio
async def test_stripe_webhook_checkout_completed(client):
    """Test checkout.session.completed webhook."""
    discord_id = "123456789"
    customer_id = "cus_test_123"
    event_id = generate_unique_event_id()

    payload = {
        "id": event_id,
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_test_123",
                "customer": customer_id,
                "metadata": {
                    "discord_id": discord_id
                }
            }
        }
    }

    payload_str = json.dumps(payload)
    sig = generate_stripe_signature(payload_str, STRIPE_WEBHOOK_SECRET)

    response = client.post(
        "/api/billing/webhook",
        content=payload_str,
        headers={"stripe-signature": sig}
    )

    assert response.status_code == 200

    # Verify database state
    user = await db.get_user_billing(discord_id)
    assert user is not None
    assert user["total_slots"] == 1
    assert user["stripe_customer_id"] == customer_id


@pytest.mark.asyncio
async def test_stripe_webhook_idempotency(client):
    """Test that the same event is not processed twice."""
    discord_id = "idempotent_user"
    customer_id = "cus_idempotent_123"
    event_id = generate_unique_event_id()

    payload = {
        "id": event_id,
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "customer": customer_id,
                "metadata": {"discord_id": discord_id}
            }
        }
    }
    payload_str = json.dumps(payload)
    sig = generate_stripe_signature(payload_str, STRIPE_WEBHOOK_SECRET)

    # First request
    response1 = client.post(
        "/api/billing/webhook",
        content=payload_str,
        headers={"stripe-signature": sig}
    )
    assert response1.status_code == 200

    # Second request with same event ID
    response2 = client.post(
        "/api/billing/webhook",
        content=payload_str,
        headers={"stripe-signature": sig}
    )
    assert response2.status_code == 200
    assert response2.json().get("info") == "already processed"

    # Verify slots were only added once
    user = await db.get_user_billing(discord_id)
    assert user["total_slots"] == 1


@pytest.mark.asyncio
async def test_stripe_webhook_multiple_purchases(client):
    """Test multiple purchases by the same user."""
    discord_id = "multi_purchase_user"
    customer_id = "cus_multi_123"

    # First purchase
    event_id_1 = generate_unique_event_id()
    payload_1 = {
        "id": event_id_1,
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "customer": customer_id,
                "metadata": {"discord_id": discord_id}
            }
        }
    }
    payload_str_1 = json.dumps(payload_1)
    sig_1 = generate_stripe_signature(payload_str_1, STRIPE_WEBHOOK_SECRET)

    response1 = client.post(
        "/api/billing/webhook",
        content=payload_str_1,
        headers={"stripe-signature": sig_1}
    )
    assert response1.status_code == 200

    # Second purchase (different event ID)
    event_id_2 = generate_unique_event_id()
    payload_2 = {
        "id": event_id_2,
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "customer": customer_id,
                "metadata": {"discord_id": discord_id}
            }
        }
    }
    payload_str_2 = json.dumps(payload_2)
    sig_2 = generate_stripe_signature(payload_str_2, STRIPE_WEBHOOK_SECRET)

    response2 = client.post(
        "/api/billing/webhook",
        content=payload_str_2,
        headers={"stripe-signature": sig_2}
    )
    assert response2.status_code == 200

    # Verify total slots
    user = await db.get_user_billing(discord_id)
    assert user["total_slots"] == 2


@pytest.mark.asyncio
async def test_stripe_webhook_subscription_deleted(client):
    """Test subscription cancellation webhook."""
    discord_id = "cancel_user"
    customer_id = "cus_cancel_123"
    guild_id = 987654321

    # Setup: Create user with slots and boost
    checkout_event_id = generate_unique_event_id()
    checkout_payload = {
        "id": checkout_event_id,
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "customer": customer_id,
                "metadata": {"discord_id": discord_id}
            }
        }
    }
    checkout_str = json.dumps(checkout_payload)
    checkout_sig = generate_stripe_signature(checkout_str, STRIPE_WEBHOOK_SECRET)

    client.post(
        "/api/billing/webhook",
        content=checkout_str,
        headers={"stripe-signature": checkout_sig}
    )

    # Add a boost
    pool = db._require_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO guild_boosts (guild_id, user_id) VALUES ($1, $2)",
            guild_id, discord_id
        )

    # Verify boost exists
    boosted = await db.is_guild_boosted(guild_id)
    assert boosted is True

    # Send subscription deleted webhook
    delete_event_id = generate_unique_event_id()
    delete_payload = {
        "id": delete_event_id,
        "type": "customer.subscription.deleted",
        "data": {
            "object": {
                "customer": customer_id
            }
        }
    }
    delete_str = json.dumps(delete_payload)
    delete_sig = generate_stripe_signature(delete_str, STRIPE_WEBHOOK_SECRET)

    response = client.post(
        "/api/billing/webhook",
        content=delete_str,
        headers={"stripe-signature": delete_sig}
    )

    assert response.status_code == 200

    # Verify state after deletion
    user = await db.get_user_billing(discord_id)
    assert user["total_slots"] == 0

    boosted_after = await db.is_guild_boosted(guild_id)
    assert boosted_after is False


@pytest.mark.asyncio
async def test_stripe_webhook_refund(client):
    """Test refund webhook handling."""
    discord_id = "refund_user"
    customer_id = "cus_refund_123"

    # Setup: Create user with 2 slots
    for i in range(2):
        event_id = generate_unique_event_id()
        payload = {
            "id": event_id,
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "customer": customer_id,
                    "metadata": {"discord_id": discord_id}
                }
            }
        }
        payload_str = json.dumps(payload)
        sig = generate_stripe_signature(payload_str, STRIPE_WEBHOOK_SECRET)
        client.post(
            "/api/billing/webhook",
            content=payload_str,
            headers={"stripe-signature": sig}
        )

    user_before = await db.get_user_billing(discord_id)
    assert user_before["total_slots"] == 2

    # Send refund webhook
    refund_event_id = generate_unique_event_id()
    refund_payload = {
        "id": refund_event_id,
        "type": "charge.refunded",
        "data": {
            "object": {
                "customer": customer_id
            }
        }
    }
    refund_str = json.dumps(refund_payload)
    refund_sig = generate_stripe_signature(refund_str, STRIPE_WEBHOOK_SECRET)

    response = client.post(
        "/api/billing/webhook",
        content=refund_str,
        headers={"stripe-signature": refund_sig}
    )

    assert response.status_code == 200

    # Verify slots decreased by 1
    user_after = await db.get_user_billing(discord_id)
    assert user_after["total_slots"] == 1


@pytest.mark.asyncio
async def test_invalid_webhook_signature(client):
    """Test that invalid signatures are rejected."""
    payload = {
        "id": "evt_invalid",
        "type": "checkout.session.completed",
        "data": {"object": {}}
    }
    payload_str = json.dumps(payload)

    # Use wrong secret
    invalid_sig = generate_stripe_signature(payload_str, "wrong_secret")

    response = client.post(
        "/api/billing/webhook",
        content=payload_str,
        headers={"stripe-signature": invalid_sig}
    )

    assert response.status_code == 400
