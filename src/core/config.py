# src/core/config.py

import os
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

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

# 本番環境ではCOOKIE_SECUREを強制的にtrue
if IS_PRODUCTION:
    COOKIE_SECURE = True
else:
    COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "false").lower() == "true"

# セッションの最大数制限
MAX_SESSIONS_PER_USER = int(os.environ.get("MAX_SESSIONS_PER_USER", "5"))

# Database
DATABASE_URL = os.environ["DATABASE_URL"]

# Stripe
STRIPE_API_KEY = os.environ.get("STRIPE_API_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
STRIPE_PRICE_ID = os.environ.get("STRIPE_PRICE_ID")

# URLs
DOMAIN = os.environ.get("DOMAIN", "http://localhost:5173")
FRONTEND_AFTER_LOGIN_URL = os.environ.get("FRONTEND_AFTER_LOGIN_URL", "https://sumirevox.com/")

# 許可されたリダイレクトURLのバリデーション
ALLOWED_REDIRECT_HOSTS = os.environ.get("ALLOWED_REDIRECT_HOSTS", "sumirevox.com,localhost").split(",")

# Discord permissions
ADMINISTRATOR = 0x8
MANAGE_GUILD = 0x20

# Cache TTL settings
GUILDS_CACHE_TTL = 30  # seconds
BOT_GUILDS_CACHE_TTL = 60  # seconds
BOT_INSTANCES_CACHE_TTL = 300  # 5 minutes

# Guild settings limits
FREE_MAX_CHARS = 50
PREMIUM_MAX_CHARS = 200
FREE_DICT_LIMIT = 10
PREMIUM_DICT_LIMIT = 100
DEFAULT_MAX_BOOSTS_PER_GUILD = 3

# Input validation limits
MAX_DICT_WORD_LENGTH = 100
MAX_DICT_READING_LENGTH = 200
MAX_AUTO_JOIN_CONFIG_SIZE = 10000  # 追加: auto_join_config の最大サイズ

# Default guild settings
DEFAULT_SETTINGS = {
    "auto_join": False,
    "auto_join_config": {},
    "max_chars": FREE_MAX_CHARS,
    "read_vc_status": False,
    "read_mention": True,
    "read_emoji": True,
    "add_suffix": False,
    "read_romaji": False,
    "read_attachments": True,
    "skip_code_blocks": True,
    "skip_urls": True,
}

# 追加: auto_join_config で許可されるキー
ALLOWED_AUTO_JOIN_CONFIG_KEYS = {
    "channel_id",
    "text_channel_id",
    "enabled",
    "notify_on_join",
    "notify_on_leave",
}

# レート制限設定
RATE_LIMIT_DEFAULT = os.environ.get("RATE_LIMIT_DEFAULT", "60/minute")
RATE_LIMIT_AUTH = os.environ.get("RATE_LIMIT_AUTH", "10/minute")
RATE_LIMIT_PAYMENT = os.environ.get("RATE_LIMIT_PAYMENT", "5/minute")


def get_allowed_origins() -> list[str]:
    """Get CORS allowed origins based on environment."""
    origins = [DOMAIN]
    if not IS_PRODUCTION:
        origins.extend([
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ])
    logger.info(f"CORS allowed origins: {origins}")
    return origins


def validate_redirect_url(url: str) -> bool:
    """Validate that a redirect URL is allowed."""
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
        return parsed.hostname in ALLOWED_REDIRECT_HOSTS
    except Exception:
        return False


# 起動時の設定検証
def validate_config():
    """Validate critical configuration on startup."""
    errors = []

    if IS_PRODUCTION:
        if not STRIPE_API_KEY or STRIPE_API_KEY.startswith("sk_test_"):
            logger.warning("Using test Stripe API key in production!")

        if len(SESSION_SECRET) < 32:
            errors.append("SESSION_SECRET must be at least 32 characters in production")

        if not os.environ.get("ENCRYPTION_KEY"):
            errors.append("ENCRYPTION_KEY is required in production")

    if errors:
        for error in errors:
            logger.error(f"Configuration error: {error}")
        raise RuntimeError("Invalid configuration. See logs for details.")


# 起動時に設定を検証
validate_config()
