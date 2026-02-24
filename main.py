# main.py

import os
import gc
import logging
import asyncio
from datetime import datetime, timezone
from contextlib import asynccontextmanager

import psutil
import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from src.core.middleware import SecurityHeadersMiddleware, RequestLoggingMiddleware

from src.core.config import (
    DATABASE_URL,
    get_allowed_origins,
    BOT_GUILDS_CACHE_TTL,
    BOT_INSTANCES_CACHE_TTL,
    IS_PRODUCTION,
)
from src.core.db import init_db, close_db, cleanup_expired_sessions, get_bot_instances
from src.core.dependencies import get_current_session
from src.services.discord import (
    clear_bot_guilds_cache,
    clear_bot_instances_cache,
    get_cache_stats,
    get_bot_instances_cached,
)
from src.routers import auth_router, guilds_router, billing_router

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
)
logger = logging.getLogger("sumire-vox-backend")

# レート制限の設定
limiter = Limiter(key_func=get_remote_address)


async def background_cleanup():
    """定期的に実行するクリーンアップタスク"""
    while True:
        try:
            await asyncio.sleep(300)
            logger.info("定期クリーンアップを開始します...")

            now = datetime.now(timezone.utc)

            deleted_sessions = await cleanup_expired_sessions()
            if deleted_sessions > 0:
                logger.info(f"期限切れのセッションを {deleted_sessions} 件削除しました。")

            gc.collect()
            logger.info("定期クリーンアップが完了しました。")
        except asyncio.CancelledError:
            logger.info("定期クリーンアップタスクを停止します。")
            break
        except Exception as e:
            logger.error(f"定期クリーンアップ中にエラーが発生しました: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    await init_db(DATABASE_URL)

    instances = await get_bot_instances()
    if not instances:
        logger.error("bot_instancesテーブルにアクティブなBotが登録されていません。")
        raise RuntimeError(
            "No active bot instances found in database. "
            "Please add at least one bot instance to the bot_instances table."
        )

    logger.info(f"Primary bot client_id loaded: {instances[0]['client_id']}")
    logger.info(f"Total active bot instances: {len(instances)}")

    cleanup_task = asyncio.create_task(background_cleanup())

    app.state.http_client = httpx.AsyncClient(timeout=20)

    try:
        yield
    finally:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass
        await app.state.http_client.aclose()
        await close_db()


# Create FastAPI app
app = FastAPI(
    title="SumireVox Backend",
    description="Backend API for SumireVox Discord Bot",
    version="1.0.0",
    lifespan=lifespan,
    # 本番環境ではドキュメントを無効化（オプション）
    docs_url=None if IS_PRODUCTION else "/docs",
    redoc_url=None if IS_PRODUCTION else "/redoc",
    openapi_url=None if IS_PRODUCTION else "/openapi.json",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# グローバル例外ハンドラ（本番環境で内部エラーを隠す）
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Handle uncaught exceptions."""
    logger.error(
        f"Unhandled exception: {type(exc).__name__}: {exc}",
        exc_info=True,
        extra={
            "path": request.url.path,
            "method": request.method,
        }
    )

    if IS_PRODUCTION:
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"}
        )

    # 開発環境ではエラー詳細を返す
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            "error": str(exc),
            "type": type(exc).__name__,
        }
    )


app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestLoggingMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Content-Type",
        "Authorization",
        "Accept",
        "Origin",
        "X-Requested-With",
    ],
    expose_headers=["X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset"],
    max_age=600,
)

# Include routers
app.include_router(auth_router)
app.include_router(guilds_router)
app.include_router(billing_router)


# Health check endpoints
@app.get("/health")
@limiter.limit("60/minute")
async def health(request: Request):
    """Basic health check."""
    return {"status": "ok"}


@app.get("/health/memory")
@limiter.limit("10/minute")
async def health_memory(request: Request):
    """Memory usage health check - requires authentication."""
    await get_current_session(request)

    process = psutil.Process(os.getpid())
    mem_info = process.memory_info()

    instances = await get_bot_instances_cached()
    cache_stats = get_cache_stats()

    return {
        "rss": f"{mem_info.rss / 1024 / 1024:.2f} MB",
        "vms": f"{mem_info.vms / 1024 / 1024:.2f} MB",
        "bot_instances_count": len(instances),
        "gc_objects_count": len(gc.get_objects()),
        **cache_stats,
    }


@app.get("/api/bot-instances")
@limiter.limit("30/minute")
async def get_bot_instances_api(request: Request):
    """Get active bot instances (public info only)."""
    instances = await get_bot_instances_cached()

    public_instances = [
        {
            "bot_name": inst["bot_name"],
            "id": inst["id"],
        }
        for inst in instances
    ]

    return {
        "instances": public_instances,
        "count": len(public_instances)
    }


@app.get("/api/bot-instances/details")
@limiter.limit("10/minute")
async def get_bot_instances_details(request: Request):
    """Get detailed bot instances info - requires authentication."""
    await get_current_session(request)

    instances = await get_bot_instances_cached()
    return {
        "instances": instances,
        "count": len(instances)
    }


@app.get("/api/me")
@limiter.limit("60/minute")
async def api_me(request: Request):
    """Get current user info (legacy endpoint)."""
    sess = await get_current_session(request)
    return {"user": {"discordId": sess.discord_user_id, "username": sess.username}}
