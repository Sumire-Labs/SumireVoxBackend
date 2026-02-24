# src/core/models.py

import json
from pydantic import BaseModel, Field, field_validator
from typing import Optional, Any

from src.core.config import (
    PREMIUM_MAX_CHARS,
    MAX_DICT_WORD_LENGTH,
    MAX_DICT_READING_LENGTH,
    MAX_AUTO_JOIN_CONFIG_SIZE,
    ALLOWED_AUTO_JOIN_CONFIG_KEYS,
)


class GuildSettingsUpdate(BaseModel):
    """Guild settings update request model."""
    auto_join: Optional[bool] = None
    auto_join_config: Optional[dict] = None
    max_chars: Optional[int] = Field(None, ge=1, le=PREMIUM_MAX_CHARS)
    read_vc_status: Optional[bool] = None
    read_mention: Optional[bool] = None
    read_emoji: Optional[bool] = None
    add_suffix: Optional[bool] = None
    read_romaji: Optional[bool] = None
    read_attachments: Optional[bool] = None
    skip_code_blocks: Optional[bool] = None
    skip_urls: Optional[bool] = None

    @field_validator('auto_join_config')
    @classmethod
    def validate_auto_join_config(cls, v: Any) -> Optional[dict]:
        if v is None:
            return v

        if not isinstance(v, dict):
            raise ValueError('auto_join_config must be a dictionary')

        # 【追加】サイズ制限チェック
        try:
            serialized = json.dumps(v)
            if len(serialized) > MAX_AUTO_JOIN_CONFIG_SIZE:
                raise ValueError(
                    f'auto_join_config is too large (max {MAX_AUTO_JOIN_CONFIG_SIZE} characters)'
                )
        except (TypeError, ValueError) as e:
            if 'too large' in str(e):
                raise
            raise ValueError('auto_join_config contains non-serializable values')

        # 【追加】許可されたキーのみ受け入れる
        unknown_keys = set(v.keys()) - ALLOWED_AUTO_JOIN_CONFIG_KEYS
        if unknown_keys:
            raise ValueError(
                f'Unknown keys in auto_join_config: {", ".join(sorted(unknown_keys))}. '
                f'Allowed keys: {", ".join(sorted(ALLOWED_AUTO_JOIN_CONFIG_KEYS))}'
            )

        # 【追加】各フィールドの型チェック
        if 'channel_id' in v and v['channel_id'] is not None:
            if not isinstance(v['channel_id'], (str, int)):
                raise ValueError('channel_id must be a string or integer')
            try:
                int(str(v['channel_id']))
            except ValueError:
                raise ValueError('channel_id must be a valid Discord snowflake ID')

        if 'text_channel_id' in v and v['text_channel_id'] is not None:
            if not isinstance(v['text_channel_id'], (str, int)):
                raise ValueError('text_channel_id must be a string or integer')
            try:
                int(str(v['text_channel_id']))
            except ValueError:
                raise ValueError('text_channel_id must be a valid Discord snowflake ID')

        if 'enabled' in v and not isinstance(v['enabled'], bool):
            raise ValueError('enabled must be a boolean')

        if 'notify_on_join' in v and not isinstance(v['notify_on_join'], bool):
            raise ValueError('notify_on_join must be a boolean')

        if 'notify_on_leave' in v and not isinstance(v['notify_on_leave'], bool):
            raise ValueError('notify_on_leave must be a boolean')

        return v

    def to_update_dict(self) -> dict:
        """Convert to dict excluding None values."""
        return {k: v for k, v in self.model_dump().items() if v is not None}


class DictEntry(BaseModel):
    """Dictionary entry request model."""
    word: str = Field(..., min_length=1, max_length=MAX_DICT_WORD_LENGTH)
    reading: str = Field(..., min_length=1, max_length=MAX_DICT_READING_LENGTH)

    @field_validator('word', 'reading')
    @classmethod
    def strip_whitespace(cls, v: str) -> str:
        return v.strip()

    # 【追加】制御文字のバリデーション
    @field_validator('word')
    @classmethod
    def validate_word(cls, v: str) -> str:
        if any(ord(c) < 32 for c in v):
            raise ValueError('word contains invalid control characters')
        return v

    @field_validator('reading')
    @classmethod
    def validate_reading(cls, v: str) -> str:
        if any(ord(c) < 32 for c in v):
            raise ValueError('reading contains invalid control characters')
        return v


class BoostRequest(BaseModel):
    """Boost/Unboost request model."""
    guild_id: str = Field(..., pattern=r'^\d+$')

    @property
    def guild_id_int(self) -> int:
        return int(self.guild_id)
