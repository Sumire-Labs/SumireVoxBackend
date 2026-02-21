# src/core/db/pool.py

from __future__ import annotations

import asyncio
import logging
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None
_init_lock = asyncio.Lock()


def _require_pool() -> asyncpg.Pool:
    """プールが初期化されていることを確認して返す"""
    if _pool is None:
        raise RuntimeError("Database pool is not initialized. Call init_db() on startup.")
    return _pool


async def init_db(database_url: str) -> None:
    """Initialize asyncpg pool and ensure required tables exist."""
    global _pool

    if _pool is not None:
        return

    async with _init_lock:
        if _pool is not None:
            return

        _pool = await asyncpg.create_pool(database_url, min_size=1, max_size=10)

        async with _pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_instances
                (
                    id        SERIAL PRIMARY KEY,
                    client_id TEXT    NOT NULL,
                    bot_name  TEXT    NOT NULL,
                    is_active BOOLEAN NOT NULL DEFAULT true
                );

                CREATE TABLE IF NOT EXISTS web_sessions
                (
                    sid             TEXT PRIMARY KEY,
                    discord_user_id TEXT        NOT NULL,
                    username        TEXT,
                    access_token    TEXT,
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                    expires_at      TIMESTAMPTZ NOT NULL
                );

                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1
                                   FROM information_schema.columns
                                   WHERE table_name = 'web_sessions'
                                     AND column_name = 'access_token') THEN
                        ALTER TABLE web_sessions
                            ADD COLUMN access_token TEXT;
                    END IF;
                END $$;

                CREATE INDEX IF NOT EXISTS idx_web_sessions_discord_user_id
                    ON web_sessions (discord_user_id);

                CREATE INDEX IF NOT EXISTS idx_web_sessions_expires_at
                    ON web_sessions (expires_at);

                CREATE TABLE IF NOT EXISTS guild_settings
                (
                    guild_id BIGINT PRIMARY KEY,
                    settings JSONB NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS dict
                (
                    guild_id BIGINT PRIMARY KEY,
                    dict     JSONB NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS users
                (
                    discord_id         TEXT PRIMARY KEY,
                    stripe_customer_id TEXT UNIQUE,
                    total_slots        INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS guild_boosts
                (
                    id         SERIAL PRIMARY KEY,
                    guild_id   BIGINT      NOT NULL,
                    user_id    TEXT        NOT NULL REFERENCES users (discord_id) ON DELETE CASCADE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );

                CREATE TABLE IF NOT EXISTS processed_stripe_events
                (
                    event_id     TEXT PRIMARY KEY,
                    processed_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );

                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1
                                   FROM information_schema.columns
                                   WHERE table_name = 'guild_boosts'
                                     AND column_name = 'created_at') THEN
                        ALTER TABLE guild_boosts
                            ADD COLUMN created_at TIMESTAMPTZ NOT NULL DEFAULT now();
                    END IF;
                END $$;

                CREATE INDEX IF NOT EXISTS idx_guild_boosts_guild_id ON guild_boosts (guild_id);
                CREATE INDEX IF NOT EXISTS idx_guild_boosts_user_id ON guild_boosts (user_id);
                """
            )
        logger.info("Database initialized successfully.")


async def close_db() -> None:
    """Close asyncpg pool."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("Database connection closed.")


async def healthcheck() -> dict[str, Any]:
    """Simple DB healthcheck helper."""
    pool = _require_pool()
    async with pool.acquire() as conn:
        value = await conn.fetchval("SELECT 1")
    return {"ok": value == 1}
