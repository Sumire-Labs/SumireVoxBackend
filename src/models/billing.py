from sqlalchemy import Column, String, BigInteger, Integer, ForeignKey
from sqlalchemy.orm import relationship, declarative_base

Base = declarative_base()

class User(Base):
    __tablename__ = "users"

    discord_id = Column(String, primary_key=True, index=True)
    stripe_customer_id = Column(String, unique=True, index=True, nullable=True)
    total_slots = Column(Integer, default=0, nullable=False)

    boosts = relationship("GuildBoost", back_populates="user", cascade="all, delete-orphan")

class GuildBoost(Base):
    __tablename__ = "guild_boosts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger, index=True, nullable=False)
    user_id = Column(String, ForeignKey("users.discord_id"), nullable=False)

    user = relationship("User", back_populates="boosts")
