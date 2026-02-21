# src/core/config.py

import os
from dotenv import load_dotenv

load_dotenv()

# Environment
ENV = os.environ.get("ENV", "development").lower()
IS_PRODUCTION = ENV == "production"

# Discord OAuth
DISCORD_CLIENT_SECRET = os.environ["DISCORD_CLIENT_SECRET"]
DISCORD_REDIRECT_URI = os.environ["DISCORD_REDIRECT_URI"]
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")

# Session
SESSION_SECRET = os.environ["SESSION_SECRET"]
SESSION_TTL_DAYS = int(os.environ.get("SESSION_TTL_DAYS", "7"))
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "true").lower() == "true"

# Database
DATABASE_URL = os.environ["DATABASE_URL"]

# Stripe
STRIPE_API_KEY = os.environ.get("STRIPE_API_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
STRIPE_PRICE_ID = os.environ.get("STRIPE_PRICE_ID")

# URLs
DOMAIN = os.environ.get("DOMAIN", "http://localhost:5173")
FRONTEND_AFTER_LOGIN_URL = os.environ.get("FRONTEND_AFTER_LOGIN_URL", "https://sumirevox.com/")

# Discord permissions
ADMINISTRATOR = 0x8
MANAGE_GUILD = 0x20

# Cache TTL settings
GUILDS_CACHE_TTL = 30  # seconds
BOT_GUILDS_CACHE_TTL = 60  # seconds
BOT_INSTANCES_CACHE_TTL = 300  # 5 minutes

# Default guild settings
DEFAULT_SETTINGS = {
    "auto_join": False,
    "auto_join_config": {},
    "max_chars": 50,
    "read_vc_status": False,
    "read_mention": True,
    "read_emoji": True,
    "add_suffix": False,
    "read_romaji": False,
    "read_attachments": True,
    "skip_code_blocks": True,
    "skip_urls": True,
}


def get_allowed_origins() -> list[str]:
    """Get CORS allowed origins based on environment."""
    origins = [DOMAIN]
    if not IS_PRODUCTION:
        origins.extend([
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ])
    return origins
