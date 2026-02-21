# main.py

import os
import gc
import logging
import asyncio
from datetime import datetime, timezone
from contextlib import asynccontextmanager

import psutil
import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.core.config import DATABASE_URL, get_allowed_origins, BOT_GUILDS_CACHE_TTL, BOT_INSTANCES_CACHE_TTL
from src.core.database import init_db, close_db, cleanup_expired_sessions, get_bot_instances
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


async def background_cleanup():
    """定期的に実行するクリーンアップタスク"""
    while True:
        try:
            await asyncio.sleep(300)  # 5分ごとに実行
            logger.info("定期クリーンアップを開始します...")

            now = datetime.now(timezone.utc)

            # 1. キャッシュのクリア（TTLベース）
            # Note: サービス層で自動的にTTL管理されているが、明示的にクリアも可能

            # 2. 期限切れセッションと古い Stripe イベントの削除
            deleted_sessions = await cleanup_expired_sessions()
            if deleted_sessions > 0:
                logger.info(f"期限切れのセッションを {deleted_sessions} 件削除しました。")

            # 3. ガベージコレクションの強制実行
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
    # Startup
    await init_db(DATABASE_URL)

    # Verify bot instances exist
    instances = await get_bot_instances()
    if not instances:
        logger.error("bot_instancesテーブルにアクティブなBotが登録されていません。")
        raise RuntimeError(
            "No active bot instances found in database. "
            "Please add at least one bot instance to the bot_instances table."
        )

    logger.info(f"Primary bot client_id loaded: {instances[0]['client_id']}")
    logger.info(f"Total active bot instances: {len(instances)}")

    # Start background task
    cleanup_task = asyncio.create_task(background_cleanup())

    # Initialize HTTP client
    app.state.http_client = httpx.AsyncClient(timeout=20)

    try:
        yield
    finally:
        # Shutdown
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
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth_router)
app.include_router(guilds_router)
app.include_router(billing_router)


# Health check endpoints
@app.get("/health")
async def health():
    """Basic health check."""
    return {"status": "ok"}


@app.get("/health/memory")
async def health_memory():
    """Memory usage health check."""
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
async def get_bot_instances_api():
    """Get active bot instances."""
    instances = await get_bot_instances_cached()
    return {
        "instances": instances,
        "count": len(instances)
    }


# For backwards compatibility with /api/me endpoint
@app.get("/api/me")
async def api_me_redirect():
    """Redirect to auth/me for backwards compatibility."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/auth/me", status_code=303)
