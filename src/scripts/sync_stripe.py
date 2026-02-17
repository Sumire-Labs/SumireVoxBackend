import os
import asyncio
import stripe
from dotenv import load_dotenv
import sys

# プロジェクトルートをパスに追加（srcをインポートできるようにするため）
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.core import database as db

load_dotenv()

stripe.api_key = os.getenv("STRIPE_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")

async def sync_all_users():
    """
    Stripe APIから最新のサブスクリプション状態を取得し、
    DBの total_slots と同期させる。
    """
    print("Starting Stripe synchronization...")
    await db.init_db(DATABASE_URL)
    
    try:
        # DBから全ユーザーを取得
        pool = db._require_pool()
        async with pool.acquire() as conn:
            users = await conn.fetch("SELECT discord_id, stripe_customer_id, total_slots FROM users WHERE stripe_customer_id IS NOT NULL")
        
        print(f"Found {len(users)} users with Stripe customer IDs.")
        
        for user in users:
            discord_id = user["discord_id"]
            customer_id = user["stripe_customer_id"]
            current_db_slots = user["total_slots"]
            
            print(f"Checking user {discord_id} (Customer: {customer_id})...")
            
            # Stripeから該当顧客の有効なサブスクリプションを取得
            subscriptions = stripe.Subscription.list(
                customer=customer_id,
                status="active"
            )
            
            # 有効なサブスクリプションの数（または特定のロジックに基づくスロット数）を計算
            # ここではシンプルに「有効なサブスク数 = スロット数」とする
            actual_slots = len(subscriptions.data)
            
            if actual_slots != current_db_slots:
                print(f"  Mismatch found! DB: {current_db_slots}, Stripe: {actual_slots}. Syncing...")
                await db.sync_user_slots(customer_id, actual_slots)
                print(f"  Successfully synced user {discord_id}.")
            else:
                print(f"  User {discord_id} is already in sync.")
                
    except Exception as e:
        print(f"Error during synchronization: {e}")
    finally:
        await db.close_db()
        print("Synchronization finished.")

if __name__ == "__main__":
    asyncio.run(sync_all_users())
