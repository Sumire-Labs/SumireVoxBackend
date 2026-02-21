# src/core/db/users.py

from __future__ import annotations

from src.core.db.pool import _require_pool


async def get_user_billing(discord_id: str) -> dict | None:
    """ユーザーの課金情報を取得する"""
    pool = _require_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT discord_id, stripe_customer_id, total_slots
            FROM users
            WHERE discord_id = $1
            """,
            discord_id,
        )
        if not row:
            return None

        boosts = await conn.fetch(
            "SELECT id, guild_id, user_id FROM guild_boosts WHERE user_id = $1",
            discord_id,
        )

        return {
            "discord_id": row["discord_id"],
            "stripe_customer_id": row["stripe_customer_id"],
            "total_slots": row["total_slots"],
            "boosts": [dict(b) for b in boosts],
        }


async def create_or_update_user(
    discord_id: str, stripe_customer_id: str | None = None
) -> None:
    """ユーザーを作成または更新する"""
    pool = _require_pool()
    async with pool.acquire() as conn:
        if stripe_customer_id:
            await conn.execute(
                """
                INSERT INTO users (discord_id, stripe_customer_id)
                VALUES ($1, $2)
                ON CONFLICT (discord_id) DO UPDATE SET stripe_customer_id = EXCLUDED.stripe_customer_id
                """,
                discord_id,
                stripe_customer_id,
            )
        else:
            await conn.execute(
                """
                INSERT INTO users (discord_id)
                VALUES ($1)
                ON CONFLICT (discord_id) DO NOTHING
                """,
                discord_id,
            )


async def add_user_slots(stripe_customer_id: str, count: int) -> None:
    """ユーザーのスロット数を追加する"""
    pool = _require_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                UPDATE users
                SET total_slots = total_slots + $1
                WHERE stripe_customer_id = $2
                """,
                count,
                stripe_customer_id,
            )


async def reset_user_slots_by_customer(stripe_customer_id: str) -> None:
    """Stripe顧客IDでユーザーのスロットをリセットする"""
    pool = _require_pool()
    async with pool.acquire() as conn:
        discord_id = await conn.fetchval(
            "SELECT discord_id FROM users WHERE stripe_customer_id = $1",
            stripe_customer_id,
        )
        if discord_id:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM guild_boosts WHERE user_id = $1", discord_id
                )
                await conn.execute(
                    "UPDATE users SET total_slots = 0 WHERE discord_id = $1",
                    discord_id,
                )


async def handle_refund_by_customer(stripe_customer_id: str) -> dict | None:
    """返金処理: スロットを減らし、必要に応じてブーストを削除する"""
    pool = _require_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            user = await conn.fetchrow(
                "SELECT discord_id, total_slots FROM users WHERE stripe_customer_id = $1 FOR UPDATE",
                stripe_customer_id,
            )
            if not user:
                return None

            discord_id = user["discord_id"]
            new_total = max(0, user["total_slots"] - 1)

            await conn.execute(
                "UPDATE users SET total_slots = $1 WHERE discord_id = $2",
                new_total,
                discord_id,
            )

            boosts = await conn.fetch(
                "SELECT id, guild_id FROM guild_boosts WHERE user_id = $1 ORDER BY created_at DESC",
                discord_id,
            )

            removed_guilds = []
            if len(boosts) > new_total:
                to_remove_count = len(boosts) - new_total
                to_remove = boosts[:to_remove_count]

                for b in to_remove:
                    await conn.execute(
                        "DELETE FROM guild_boosts WHERE id = $1", b["id"]
                    )
                    removed_guilds.append(str(b["guild_id"]))

            return {
                "discord_id": discord_id,
                "old_total": user["total_slots"],
                "new_total": new_total,
                "removed_guilds": removed_guilds,
            }
