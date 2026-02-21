# src/routers/__init__.py

from src.routers.auth import router as auth_router
from src.routers.guilds import router as guilds_router
from src.routers.billing import router as billing_router

__all__ = ["auth_router", "guilds_router", "billing_router"]
