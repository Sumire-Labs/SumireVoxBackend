# src/schemas/billing.py

from pydantic import BaseModel
from typing import List, Optional

class GuildBoostSchema(BaseModel):
    id: int
    guild_id: int
    user_id: str

    class Config:
        from_attributes = True

class UserSchema(BaseModel):
    discord_id: str
    stripe_customer_id: Optional[str] = None
    total_slots: int
    boosts: List[GuildBoostSchema] = []

    class Config:
        from_attributes = True

class UserUpdate(BaseModel):
    total_slots: Optional[int] = None
    stripe_customer_id: Optional[str] = None

class GuildBoostCreate(BaseModel):
    guild_id: int
    user_id: str
