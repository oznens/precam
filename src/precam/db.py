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


class Wallet(SQLModel, table=True):
    address: str = Field(primary_key=True)
    label: str = ""
    discovered_via: str = ""
    discovered_at: datetime = Field(default_factory=datetime.utcnow)
    last_refreshed_at: Optional[datetime] = None
    is_watched: bool = False


class Trade(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    wallet: str = Field(index=True)
    mint: str = Field(index=True)
    side: str
    amount_token: float
    amount_usd: float
    price_usd: float
    tx_sig: str = Field(index=True)
    block_time: datetime
    source: str = ""


class Position(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    wallet: str = Field(index=True)
    mint: str = Field(index=True)
    status: str = "open"
    opened_at: datetime
    closed_at: Optional[datetime] = None
    bought_token: float = 0.0
    sold_token: float = 0.0
    bought_usd: float = 0.0
    sold_usd: float = 0.0
    avg_buy_price: float = 0.0
    avg_sell_price: float = 0.0
    realized_pnl_usd: float = 0.0
    realized_pnl_pct: float = 0.0


class WalletStat(SQLModel, table=True):
    wallet: str = Field(primary_key=True)
    total_positions: int = 0
    closed_positions: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    expectancy: float = 0.0
    total_realized_pnl_usd: float = 0.0
    updated_at: datetime = Field(default_factory=datetime.utcnow)


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
