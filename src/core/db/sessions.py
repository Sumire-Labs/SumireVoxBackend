# src/core/db/sessions.py

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from src.core.crypto import encrypt, decrypt
from src.core.db.pool import _require_pool

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WebSession:
    sid: str
    discord_user_id: str
    username: str | None
    access_token: str | None
    expires_at: datetime


async def create_session(
    *,
    sid: str,
    discord_user_id: str,
    username: str | None,
    access_token: str | None,
    expires_at: datetime,
) -> None:
    """新しいセッションを作成する"""
    pool = _require_pool()
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
    """SIDでセッションを取得する（期限切れは除外）"""
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
        asyncio.create_task(_delete_session_background(sid))
        return None

    decrypted_token = None
    if row["access_token"]:
        decrypted_token = decrypt(row["access_token"])
        if decrypted_token is None:
            logger.warning(f"Session {sid[:8]}... invalidated due to decryption failure.")
            asyncio.create_task(_delete_session_background(sid))
            return None

    return WebSession(
        sid=row["sid"],
        discord_user_id=row["discord_user_id"],
        username=row["username"],
        access_token=decrypted_token,
        expires_at=expires_at,
    )


async def _delete_session_background(sid: str) -> None:
    """バックグラウンドでセッションを削除する"""
    try:
        await delete_session(sid)
    except Exception as e:
        logger.error(f"Failed to delete session {sid[:8]}...: {e}")


async def delete_session(sid: str) -> None:
    """セッションを削除する"""
    pool = _require_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM web_sessions WHERE sid = $1", sid)


async def cleanup_expired_sessions(limit: int = 1000) -> int:
    """期限切れセッションと古いStripeイベントを削除する"""
    pool = _require_pool()
    async with pool.acquire() as conn:
        status: str = await conn.execute(
            """
            DELETE
            FROM web_sessions
            WHERE sid IN (SELECT sid
                          FROM web_sessions
                          WHERE expires_at <= now()
                          ORDER BY expires_at ASC
                          LIMIT $1)
            """,
            limit,
        )

        await conn.execute(
            "DELETE FROM processed_stripe_events WHERE processed_at < now() - interval '30 days'"
        )

    try:
        return int(status.split()[-1])
    except Exception:
        return 0
