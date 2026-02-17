import pytest
import stripe
from fastapi.testclient import TestClient
from main import app, STRIPE_WEBHOOK_SECRET
import src.core.database as db
import asyncio
import os
import json

# テスト用のDB URL（環境変数から取得するか、デフォルトを使用）
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://user:password@localhost:5432/sumire_vox_test")

@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"

@pytest.fixture(autouse=True)
async def setup_db():
    # テストの前にDBを初期化
    await db.init_db(TEST_DATABASE_URL)
    
    # 既存のデータをクリア（テストの独立性を保つため）
    async with db._require_pool().acquire() as conn:
        await conn.execute("TRUNCATE users, guild_boosts, web_sessions CASCADE")
    
    yield
    
    # 必要に応じてクリーンアップ

@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c

def generate_stripe_signature(payload: str, secret: str):
    import hmac
    import hashlib
    import time
    timestamp = str(int(time.time()))
    signed_payload = f"{timestamp}.{payload}"
    signature = hmac.new(
        secret.encode(),
        signed_payload.encode(),
        hashlib.sha256
    ).hexdigest()
    return f"t={timestamp},v1={signature}"

@pytest.mark.asyncio
async def test_stripe_webhook_flow(client):
    # 1. checkout.session.completed のテスト
    discord_id = "123456789"
    customer_id = "cus_test_123"
    
    payload = {
        "id": "evt_test_123",
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
    
    # DBの状態を確認
    user = await db.get_user_billing(discord_id)
    assert user is not None
    assert user["total_slots"] == 1
    assert user["stripe_customer_id"] == customer_id

    # 2. もう一度同じイベントが来てもスロットが増えるか（複数購入ケースの想定）
    # ※現在の実装では checkout.session.completed が来るたびに add_user_slots(customer_id, 1) が呼ばれる
    response = client.post(
        "/api/billing/webhook",
        content=payload_str,
        headers={"stripe-signature": sig}
    )
    assert response.status_code == 200
    user = await db.get_user_billing(discord_id)
    assert user["total_slots"] == 2

    # 3. ギルドブーストの適用（Bot側のロジックだがDB経由でテスト可能）
    guild_id = 987654321
    # Botの activate_guild_boost 相当の操作
    # 本来はBot側のコードを呼び出すべきだが、ここではDBの整合性を確認
    async with db._require_pool().acquire() as conn:
        await conn.execute("INSERT INTO guild_boosts (guild_id, user_id) VALUES ($1, $2)", guild_id, discord_id)
    
    boosted = await db.is_guild_boosted(guild_id)
    assert boosted is True

    # 4. customer.subscription.deleted のテスト
    payload_del = {
        "id": "evt_test_del",
        "type": "customer.subscription.deleted",
        "data": {
            "object": {
                "customer": customer_id
            }
        }
    }
    
    payload_del_str = json.dumps(payload_del)
    sig_del = generate_stripe_signature(payload_del_str, STRIPE_WEBHOOK_SECRET)
    
    response = client.post(
        "/api/billing/webhook",
        content=payload_del_str,
        headers={"stripe-signature": sig_del}
    )
    
    assert response.status_code == 200
    
    # DBの状態を確認
    user = await db.get_user_billing(discord_id)
    assert user["total_slots"] == 0
    
    boosted_after = await db.is_guild_boosted(guild_id)
    assert boosted_after is False

@pytest.mark.asyncio
async def test_race_condition_webhook(client):
    """
    同時に複数のWebhookが届いた場合の挙動（Race Conditionの簡易チェック）
    """
    discord_id = "race_user"
    customer_id = "cus_race_123"
    
    payload = {
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

    # 同時に5つのリクエストを投げる
    # Note: TestClientは同期的なので、実際にはループで回すか、httpx.AsyncClientを使う
    # ここではDBのUPDATEアトミック性を信頼しているが、コード上での競合を確認
    for _ in range(5):
        client.post(
            "/api/billing/webhook",
            content=payload_str,
            headers={"stripe-signature": sig}
        )
    
    user = await db.get_user_billing(discord_id)
    # 5回分、スロットが増えているはず (total_slots = total_slots + 1 なので atomic)
    assert user["total_slots"] == 5
