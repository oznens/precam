from datetime import datetime
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import Field, SQLModel

from .config import settings


class Kol(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    handle: str = Field(index=True, unique=True)
    weight: float = 1.0
    notes: str = ""
    added_at: datetime = Field(default_factory=datetime.utcnow)
    last_checked_at: Optional[datetime] = None


class Tweet(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    tweet_id: str = Field(index=True, unique=True)
    handle: str = Field(index=True)
    posted_at: datetime
    text: str
    url: str
    seen_at: datetime = Field(default_factory=datetime.utcnow)


class Signal(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    mint: str = Field(index=True)
    handle: str = Field(index=True)
    tweet_id: str = Field(index=True)
    tweet_url: str
    name: str = ""
    symbol: str = ""
    price_usd: float = 0.0
    liquidity_usd: float = 0.0
    fdv_usd: float = 0.0
    pair_created_at: Optional[datetime] = None
    age_min: float = 0.0
    score: float = 0.0
    is_early: bool = False
    dex_url: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)


engine = create_async_engine(settings.database_url, echo=False, future=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


async def session() -> AsyncSession:
    return SessionLocal()
