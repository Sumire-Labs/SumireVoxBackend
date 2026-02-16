from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
import asyncio

import asyncpg


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
                  user_id TEXT NOT NULL REFERENCES users(discord_id) ON DELETE CASCADE
                );

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
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO web_sessions (sid, discord_user_id, username, access_token, expires_at)
            VALUES ($1, $2, $3, $4, $5)
            """,
            sid,
            discord_user_id,
            username,
            access_token,
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
        access_token=row["access_token"],
        expires_at=expires_at,
    )


async def delete_session(sid: str) -> None:
    pool = _require_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM web_sessions WHERE sid = $1", sid)


async def cleanup_expired_sessions(limit: int = 1000) -> int:
    """
    Optional utility: delete expired sessions in batches.
    Returns deleted row count for this call.
    """
    pool = _require_pool()
    async with pool.acquire() as conn:
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