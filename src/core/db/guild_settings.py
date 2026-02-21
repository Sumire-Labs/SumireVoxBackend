# src/core/db/guild_settings.py

from __future__ import annotations

import json

from src.core.db.pool import _require_pool


async def get_guild_settings(guild_id: int) -> dict:
    """ギルド設定を取得する"""
    pool = _require_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT settings FROM guild_settings WHERE guild_id = $1", guild_id
        )
        if row:
            raw_data = row["settings"]
            if isinstance(raw_data, str):
                return json.loads(raw_data)
            return raw_data
        return {}


async def update_guild_settings(guild_id: int, settings: dict) -> None:
    """ギルド設定を更新する（UPSERT）"""
    pool = _require_pool()
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