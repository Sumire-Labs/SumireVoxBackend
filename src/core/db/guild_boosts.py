# src/core/db/guild_boosts.py

from __future__ import annotations

from src.core.db.pool import _require_pool


async def get_guild_boost_count(guild_id: int) -> int:
    """ギルドのブースト数を取得する"""
    pool = _require_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM guild_boosts WHERE guild_id = $1::BIGINT",
            guild_id,
        )


async def get_guild_boost_counts_batch(guild_ids: list[int]) -> dict[int, int]:
    """複数ギルドのブースト数を一括取得する"""
    if not guild_ids:
        return {}

    pool = _require_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT guild_id, COUNT(*) as count
            FROM guild_boosts
            WHERE guild_id = ANY($1::BIGINT[])
            GROUP BY guild_id
            """,
            guild_ids,
        )

    result = {guild_id: 0 for guild_id in guild_ids}
    for row in rows:
        result[row["guild_id"]] = row["count"]
    return result


async def is_guild_boosted(guild_id: int) -> bool:
    """ギルドがブーストされているか確認する"""
    pool = _require_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM guild_boosts WHERE guild_id = $1::BIGINT)",
            guild_id,
        )


async def activate_guild_boost(guild_id: int, user_id: str, max_boosts: int) -> bool:
    """ギルドにブーストを追加する"""
    pool = _require_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # ユーザーの総スロット数を取得（行ロック）
            user = await conn.fetchrow(
                "SELECT total_slots FROM users WHERE discord_id = $1 FOR UPDATE",
                user_id,
            )
            if not user:
                return False

            total_slots = user["total_slots"]

            # 使用中スロット数を取得
            used_slots = await conn.fetchval(
                "SELECT COUNT(*) FROM guild_boosts WHERE user_id = $1",
                user_id,
            )

            if used_slots >= total_slots:
                return False

            # ギルドに対するアドバイザリーロック
            await conn.execute("SELECT pg_advisory_xact_lock($1)", guild_id)

            # ギルドの現在のブースト数を確認
            current_guild_boosts = await conn.fetchval(
                "SELECT COUNT(*) FROM guild_boosts WHERE guild_id = $1::BIGINT",
                guild_id,
            )
            if current_guild_boosts >= max_boosts:
                return False

            # ブーストを追加
            await conn.execute(
                "INSERT INTO guild_boosts (guild_id, user_id) VALUES ($1::BIGINT, $2)",
                guild_id,
                user_id,
            )
            return True


async def deactivate_guild_boost(guild_id: int, user_id: str) -> bool:
    """ギルドからブーストを削除する"""
    pool = _require_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT ctid FROM guild_boosts WHERE guild_id = $1::BIGINT AND user_id = $2 LIMIT 1 FOR UPDATE",
                guild_id,
                user_id,
            )
            if not row:
                return False

            result = await conn.execute(
                "DELETE FROM guild_boosts WHERE ctid = $1",
                row["ctid"],
            )
            return result == "DELETE 1"
