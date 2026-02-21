# src/core/db/bot_instances.py

from __future__ import annotations

from src.core.db.pool import _require_pool


async def get_bot_instances() -> list[dict]:
    """アクティブなBotインスタンスを取得する"""
    pool = _require_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, client_id, bot_name, is_active FROM bot_instances WHERE is_active = true ORDER BY id ASC"
        )
        return [dict(r) for r in rows]
