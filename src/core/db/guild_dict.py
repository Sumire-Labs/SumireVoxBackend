# src/core/db/guild_dict.py

from __future__ import annotations

import json

from src.core.db.pool import _require_pool


async def get_guild_dict(guild_id: int) -> dict:
    """ギルド辞書を取得する"""
    pool = _require_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT dict FROM dict WHERE guild_id = $1", guild_id
        )
        if row:
            raw_data = row["dict"]
            if isinstance(raw_data, str):
                return json.loads(raw_data)
            return raw_data
        return {}


async def update_guild_dict(guild_id: int, dict_data: dict) -> None:
    """ギルド辞書を更新する（UPSERT）"""
    pool = _require_pool()
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
