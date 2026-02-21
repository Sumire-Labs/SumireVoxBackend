# src/core/db/__init__.py

from src.core.db.pool import init_db, close_db, healthcheck
from src.core.db.sessions import (
    WebSession,
    create_session,
    get_session_by_sid,
    delete_session,
    cleanup_expired_sessions,
)
from src.core.db.guild_settings import (
    get_guild_settings,
    update_guild_settings,
)
from src.core.db.guild_dict import (
    get_guild_dict,
    update_guild_dict,
)
from src.core.db.users import (
    get_user_billing,
    create_or_update_user,
    add_user_slots,
    reset_user_slots_by_customer,
    handle_refund_by_customer,
)
from src.core.db.guild_boosts import (
    get_guild_boost_count,
    get_guild_boost_counts_batch,
    is_guild_boosted,
    activate_guild_boost,
    deactivate_guild_boost,
)
from src.core.db.stripe_events import (
    is_event_processed,
    mark_event_processed,
)
from src.core.db.bot_instances import (
    get_bot_instances,
)

__all__ = [
    # pool
    "init_db",
    "close_db",
    "healthcheck",
    # sessions
    "WebSession",
    "create_session",
    "get_session_by_sid",
    "delete_session",
    "cleanup_expired_sessions",
    # guild_settings
    "get_guild_settings",
    "update_guild_settings",
    # guild_dict
    "get_guild_dict",
    "update_guild_dict",
    # users
    "get_user_billing",
    "create_or_update_user",
    "add_user_slots",
    "reset_user_slots_by_customer",
    "handle_refund_by_customer",
    # guild_boosts
    "get_guild_boost_count",
    "get_guild_boost_counts_batch",
    "is_guild_boosted",
    "activate_guild_boost",
    "deactivate_guild_boost",
    # stripe_events
    "is_event_processed",
    "mark_event_processed",
    # bot_instances
    "get_bot_instances",
]
