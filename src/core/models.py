# src/core/models.py

from pydantic import BaseModel, Field, field_validator
from typing import Optional

from src.core.config import (
    PREMIUM_MAX_CHARS,
    MAX_DICT_WORD_LENGTH,
    MAX_DICT_READING_LENGTH,
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
    def validate_auto_join_config(cls, v):
        if v is not None and not isinstance(v, dict):
            raise ValueError('auto_join_config must be a dictionary')
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


class BoostRequest(BaseModel):
    """Boost/Unboost request model."""
    guild_id: str = Field(..., pattern=r'^\d+$')

    @property
    def guild_id_int(self) -> int:
        return int(self.guild_id)
