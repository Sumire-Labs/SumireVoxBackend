# src/core/db/stripe_events.py

from __future__ import annotations

from src.core.db.pool import _require_pool


async def is_event_processed(event_id: str) -> bool:
    """Stripeイベントが処理済みか確認する"""
    pool = _require_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM processed_stripe_events WHERE event_id = $1)",
            event_id,
        )


async def mark_event_processed(event_id: str) -> None:
    """Stripeイベントを処理済みとしてマークする"""
    pool = _require_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO processed_stripe_events (event_id) VALUES ($1) ON CONFLICT DO NOTHING",
            event_id,
        )
