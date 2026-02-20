# src/core/database.py

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
import asyncio

import asyncpg

from src.core.crypto import encrypt, decrypt


@dataclass(frozen=True, slots=True)
class WebSession:
    sid: str
    discord_user_id: str
    username: str | None
    access_token: str | None
    expires_at: datetime


_pool: asyncpg.Pool | None = None
_init_lock = asyncio.Lock()


def _require_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool is not initialized. Call init_db() on startup.")
    return _pool


async def init_db(database_url: str) -> None:
    """
    Initialize asyncpg pool and ensure required tables exist.
    Safe to call multiple times.
    """
    global _pool

    if _pool is not None:
        return

    async with _init_lock:
        if _pool is not None:
            return

        _pool = await asyncpg.create_pool(database_url, min_size=1, max_size=10)

        async with _pool.acquire() as conn:
            # Ensure tables exist
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_instances (
                  id SERIAL PRIMARY KEY,
                  client_id TEXT NOT NULL,
                  bot_name TEXT NOT NULL,
                  is_active BOOLEAN NOT NULL DEFAULT true
                );

                CREATE TABLE IF NOT EXISTS web_sessions (
                  sid TEXT PRIMARY KEY,
                  discord_user_id TEXT NOT NULL,
                  username TEXT,
                  access_token TEXT,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                  expires_at TIMESTAMPTZ NOT NULL
                );

                -- Migration: add access_token if missing (for existing tables)
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                   WHERE table_name='web_sessions' AND column_name='access_token') THEN
                        ALTER TABLE web_sessions ADD COLUMN access_token TEXT;
                    END IF;
                END $$;

                CREATE INDEX IF NOT EXISTS idx_web_sessions_discord_user_id
                  ON web_sessions (discord_user_id);

                CREATE INDEX IF NOT EXISTS idx_web_sessions_expires_at
                  ON web_sessions (expires_at);

                CREATE TABLE IF NOT EXISTS guild_settings (
                  guild_id BIGINT PRIMARY KEY,
                  settings JSONB NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS dict (
                  guild_id BIGINT PRIMARY KEY,
                  dict JSONB NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS users (
                  discord_id TEXT PRIMARY KEY,
                  stripe_customer_id TEXT UNIQUE,
                  total_slots INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS guild_boosts (
                  id SERIAL PRIMARY KEY,
                  guild_id BIGINT NOT NULL,
                  user_id TEXT NOT NULL REFERENCES users(discord_id) ON DELETE CASCADE,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );

                -- Idempotency for Stripe
                CREATE TABLE IF NOT EXISTS processed_stripe_events (
                  event_id TEXT PRIMARY KEY,
                  processed_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );

                -- Migration: add created_at if missing
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                   WHERE table_name='guild_boosts' AND column_name='created_at') THEN
                        ALTER TABLE guild_boosts ADD COLUMN created_at TIMESTAMPTZ NOT NULL DEFAULT now();
                    END IF;
                END $$;

                CREATE INDEX IF NOT EXISTS idx_guild_boosts_guild_id ON guild_boosts(guild_id);
                CREATE INDEX IF NOT EXISTS idx_guild_boosts_user_id ON guild_boosts(user_id);
                """
            )


async def close_db() -> None:
    """
    Close asyncpg pool.
    Call this from FastAPI shutdown event.
    """
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def create_session(*, sid: str, discord_user_id: str, username: str | None, access_token: str | None, expires_at: datetime) -> None:
    pool = _require_pool()
    # トークンを暗号化
    encrypted_token = encrypt(access_token) if access_token else None

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO web_sessions (sid, discord_user_id, username, access_token, expires_at)
            VALUES ($1, $2, $3, $4, $5)
            """,
            sid,
            discord_user_id,
            username,
            encrypted_token,
            expires_at,
        )


async def get_session_by_sid(sid: str) -> WebSession | None:
    """
    Returns session if exists and not expired.
    If expired, deletes it and returns None.
    """
    pool = _require_pool()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT sid, discord_user_id, username, access_token, expires_at
            FROM web_sessions
            WHERE sid = $1
            """,
            sid,
        )

    if not row:
        return None

    expires_at: datetime = row["expires_at"]
    if expires_at <= datetime.now(timezone.utc):
        await delete_session(sid)
        return None

    return WebSession(
        sid=row["sid"],
        discord_user_id=row["discord_user_id"],
        username=row["username"],
        access_token=decrypt(row["access_token"]) if row["access_token"] else None,
        expires_at=expires_at,
    )


async def delete_session(sid: str) -> None:
    pool = _require_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM web_sessions WHERE sid = $1", sid)


async def cleanup_expired_sessions(limit: int = 1000) -> int:
    """
    Delete expired sessions and old processed stripe events.
    Returns deleted session count.
    """
    pool = _require_pool()
    async with pool.acquire() as conn:
        # 1. Cleanup sessions
        status: str = await conn.execute(
            """
            DELETE FROM web_sessions
            WHERE sid IN (
              SELECT sid
              FROM web_sessions
              WHERE expires_at <= now()
              ORDER BY expires_at ASC
              LIMIT $1
            )
            """,
            limit,
        )
        
        # 2. Cleanup old processed_stripe_events (older than 30 days)
        await conn.execute(
            "DELETE FROM processed_stripe_events WHERE processed_at < now() - interval '30 days'"
        )

    try:
        return int(status.split()[-1])
    except Exception:
        return 0


async def healthcheck() -> dict[str, Any]:
    """
    Simple DB healthcheck helper (optional).
    """
    pool = _require_pool()
    async with pool.acquire() as conn:
        value = await conn.fetchval("SELECT 1")
    return {"ok": value == 1}


async def get_guild_settings(guild_id: int) -> dict:
    pool = _require_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT settings FROM guild_settings WHERE guild_id = $1", guild_id)
        if row:
            import json
            raw_data = row["settings"]
            if isinstance(raw_data, str):
                return json.loads(raw_data)
            return raw_data
        return {}


async def update_guild_settings(guild_id: int, settings: dict) -> None:
    pool = _require_pool()
    import json
    settings_json = json.dumps(settings)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO guild_settings (guild_id, settings)
            VALUES ($1, $2)
            ON CONFLICT (guild_id) DO UPDATE SET settings = EXCLUDED.settings
            """,
            guild_id,
            settings_json,
        )


async def get_guild_dict(guild_id: int) -> dict:
    pool = _require_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT dict FROM dict WHERE guild_id = $1", guild_id)
        if row:
            import json
            raw_data = row["dict"]
            if isinstance(raw_data, str):
                return json.loads(raw_data)
            return raw_data
        return {}


async def update_guild_dict(guild_id: int, dict_data: dict) -> None:
    pool = _require_pool()
    import json
    dict_json = json.dumps(dict_data)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO dict (guild_id, dict)
            VALUES ($1, $2)
            ON CONFLICT (guild_id) DO UPDATE SET dict = EXCLUDED.dict
            """,
            guild_id,
            dict_json,
        )


# --- Billing (Stripe) ---

async def get_user_billing(discord_id: str) -> dict | None:
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
            discord_id
        )
        
        return {
            "discord_id": row["discord_id"],
            "stripe_customer_id": row["stripe_customer_id"],
            "total_slots": row["total_slots"],
            "boosts": [dict(b) for b in boosts]
        }


async def create_or_update_user(discord_id: str, stripe_customer_id: str | None = None) -> None:
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


async def sync_user_slots(stripe_customer_id: str, total_slots: int) -> None:
    """Force sync slots to a specific value (used by sync script)"""
    pool = _require_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            user = await conn.fetchrow(
                "SELECT discord_id, total_slots FROM users WHERE stripe_customer_id = $1 FOR UPDATE",
                stripe_customer_id
            )
            if not user:
                return

            discord_id = user["discord_id"]
            
            # Update slots
            await conn.execute(
                "UPDATE users SET total_slots = $1 WHERE discord_id = $2",
                total_slots, discord_id
            )

            # If new total is less than current boosts, remove excess
            boosts = await conn.fetch(
                "SELECT id FROM guild_boosts WHERE user_id = $1 ORDER BY created_at DESC",
                discord_id
            )
            if len(boosts) > total_slots:
                to_remove = boosts[:len(boosts) - total_slots]
                for b in to_remove:
                    await conn.execute("DELETE FROM guild_boosts WHERE id = $1", b["id"])


async def reset_user_slots_by_customer(stripe_customer_id: str) -> None:
    pool = _require_pool()
    async with pool.acquire() as conn:
        # Get discord_id first to delete boosts
        discord_id = await conn.fetchval(
            "SELECT discord_id FROM users WHERE stripe_customer_id = $1",
            stripe_customer_id
        )
        if discord_id:
            async with conn.transaction():
                await conn.execute("DELETE FROM guild_boosts WHERE user_id = $1", discord_id)
                await conn.execute(
                    "UPDATE users SET total_slots = 0 WHERE discord_id = $1",
                    discord_id
                )


async def handle_refund_by_customer(stripe_customer_id: str) -> dict | None:
    """
    Handle a refund: decrement total_slots and remove boosts if they exceed the new total.
    Returns info about the changes for logging.
    """
    pool = _require_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            user = await conn.fetchrow(
                "SELECT discord_id, total_slots FROM users WHERE stripe_customer_id = $1 FOR UPDATE",
                stripe_customer_id
            )
            if not user:
                return None

            discord_id = user["discord_id"]
            new_total = max(0, user["total_slots"] - 1)

            # Update slots
            await conn.execute(
                "UPDATE users SET total_slots = $1 WHERE discord_id = $2",
                new_total, discord_id
            )

            # Check if we need to remove boosts
            boosts = await conn.fetch(
                "SELECT id, guild_id FROM guild_boosts WHERE user_id = $1 ORDER BY created_at DESC",
                discord_id
            )

            removed_guilds = []
            if len(boosts) > new_total:
                to_remove_count = len(boosts) - new_total
                to_remove = boosts[:to_remove_count]
                
                for b in to_remove:
                    await conn.execute("DELETE FROM guild_boosts WHERE id = $1", b["id"])
                    removed_guilds.append(str(b["guild_id"]))

            return {
                "discord_id": discord_id,
                "old_total": user["total_slots"],
                "new_total": new_total,
                "removed_guilds": removed_guilds
            }


async def is_event_processed(event_id: str) -> bool:
    pool = _require_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT EXISTS(SELECT 1 FROM processed_stripe_events WHERE event_id = $1)", event_id)


async def mark_event_processed(event_id: str) -> None:
    pool = _require_pool()
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO processed_stripe_events (event_id) VALUES ($1) ON CONFLICT DO NOTHING", event_id)


async def delete_guild_boosts_by_guild(guild_id: int) -> int:
    """Remove all boosts from a guild (e.g. when bot is kicked)"""
    pool = _require_pool()
    async with pool.acquire() as conn:
        status = await conn.execute("DELETE FROM guild_boosts WHERE guild_id = $1", guild_id)
        try:
            return int(status.split()[-1])
        except Exception:
            return 0


async def deactivate_guild_boost(guild_id: int, user_id: str) -> bool:
    """
    Remove a single boost from a guild for a specific user.
    Uses CTID to delete only one row if multiple exist.
    """
    pool = _require_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Get one row's CTID
            row = await conn.fetchrow(
                "SELECT ctid FROM guild_boosts WHERE guild_id = $1::BIGINT AND user_id = $2 LIMIT 1 FOR UPDATE",
                guild_id,
                user_id
            )
            if not row:
                return False
            
            # Delete by CTID
            result = await conn.execute(
                "DELETE FROM guild_boosts WHERE ctid = $1",
                row["ctid"]
            )
            return result == "DELETE 1"


async def activate_guild_boost(guild_id: int, user_id: str, max_boosts: int = 3) -> bool:
    """
    Activate a boost for a guild using a user's slot.
    Checks for slot availability and existing boosts count.
    """
    pool = _require_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # 1. Lock user and check slots
            user = await conn.fetchrow(
                "SELECT total_slots FROM users WHERE discord_id = $1 FOR UPDATE",
                user_id
            )
            if not user:
                return False  # User not found or no slots
            
            total_slots = user["total_slots"]
            
            # 2. Count used slots by this user
            used_slots = await conn.fetchval(
                "SELECT COUNT(*) FROM guild_boosts WHERE user_id = $1",
                user_id
            )
            
            if used_slots >= total_slots:
                return False  # No available slots
            
            # 3. Check guild boost count (limit check)
            current_guild_boosts = await conn.fetchval(
                "SELECT COUNT(*) FROM guild_boosts WHERE guild_id = $1::BIGINT",
                guild_id
            )
            if current_guild_boosts >= max_boosts:
                return False
                
            # 4. Insert boost
            await conn.execute(
                "INSERT INTO guild_boosts (guild_id, user_id) VALUES ($1::BIGINT, $2)",
                guild_id,
                user_id
            )
            return True


async def get_guild_boost_count(guild_id: int) -> int:
    """
    Get the number of boosts for a guild.
    """
    pool = _require_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM guild_boosts WHERE guild_id = $1::BIGINT",
            guild_id
        )


async def is_guild_boosted(guild_id: int) -> bool:
    """
    Check if a guild is boosted.
    """
    pool = _require_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM guild_boosts WHERE guild_id = $1::BIGINT)",
            guild_id
        )


# --- Bot Instances ---

async def get_bot_instances() -> list[dict]:
    """
    Fetch all active bot instances from the database.
    """
    pool = _require_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, client_id, bot_name, is_active FROM bot_instances WHERE is_active = true ORDER BY id ASC"
        )
        return [dict(r) for r in rows]


async def add_bot_instance(client_id: str, bot_name: str, is_active: bool = True) -> int:
    """
    Add a new bot instance to the database.
    """
    pool = _require_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO bot_instances (client_id, bot_name, is_active) VALUES ($1, $2, $3) RETURNING id",
            client_id,
            bot_name,
            is_active
        )