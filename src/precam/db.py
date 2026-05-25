from datetime import datetime
from typing import Optional

from sqlalchemy import text
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
    watched_at: Optional[datetime] = None
    last_seen_sig: Optional[str] = None


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
    avg_trade_size_usd: float = 0.0
    avg_hold_minutes: float = 0.0
    trades_per_day: float = 0.0
    is_likely_bot: bool = False
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class PumpToken(SQLModel, table=True):
    mint: str = Field(primary_key=True)
    name: str = ""
    symbol: str = ""
    uri: str = ""
    creator: str = Field(index=True, default="")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    create_sig: str = ""
    initial_buy_sol: float = 0.0
    v_sol_in_bc: float = 0.0
    v_tokens_in_bc: float = 0.0

    market_cap_sol: float = 0.0
    bonding_progress: float = 0.0
    creator_share: float = 0.0
    top10_share: float = 0.0
    holders_count: int = 0
    mint_auth_revoked: bool = False
    freeze_auth_revoked: bool = False

    rug_risk: float = 0.0
    is_clean: bool = False
    last_checked_at: Optional[datetime] = None
    alerted: bool = False


class BacktestRun(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    source: str
    strategy: str
    tp_pct: float
    sl_pct: float
    max_hold_min: int
    entry_delay_min: int = 0
    started_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None
    total_trades: int = 0
    closed_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    avg_pnl_pct: float = 0.0
    median_pnl_pct: float = 0.0
    expectancy: float = 0.0
    sum_pnl_pct: float = 0.0


class BacktestTrade(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: int = Field(index=True)
    source_key: str = Field(index=True)
    source_ref: str = Field(index=True)
    mint: str = Field(index=True)
    symbol: str = ""
    entry_ts: datetime
    entry_price: float
    exit_ts: Optional[datetime] = None
    exit_price: float = 0.0
    exit_reason: str = ""
    pnl_pct: float = 0.0
    hold_minutes: float = 0.0
    max_favourable_pct: float = 0.0
    max_adverse_pct: float = 0.0


class PaperPortfolio(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    starting_balance_usd: float
    current_cash_usd: float
    total_realized_pnl_usd: float = 0.0
    total_fees_usd: float = 0.0
    total_slippage_usd: float = 0.0
    positions_opened: int = 0
    positions_closed: int = 0
    position_size_usd: float = 50.0
    max_concurrent: int = 10
    slippage_pct: float = 1.0
    fee_usd: float = 0.50
    tp_pct: float = 100.0
    sl_pct: float = -30.0
    max_hold_min: int = 720
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class PaperPosition(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    portfolio_id: int = Field(index=True)
    source_kind: str = Field(index=True)
    source_ref: str = Field(index=True)
    source_key: str = Field(index=True, default="")
    mint: str = Field(index=True)
    symbol: str = ""
    name: str = ""

    opened_at: datetime = Field(default_factory=datetime.utcnow)
    entry_price_market: float = 0.0
    entry_price_filled: float = 0.0
    entry_amount_usd: float = 0.0
    entry_tokens: float = 0.0
    entry_fee_usd: float = 0.0
    entry_slippage_usd: float = 0.0

    tp_pct: float
    sl_pct: float
    max_hold_min: int

    status: str = Field(index=True, default="open")
    last_price: float = 0.0
    last_checked_at: Optional[datetime] = None
    max_favourable_pct: float = 0.0
    max_adverse_pct: float = 0.0

    closed_at: Optional[datetime] = None
    exit_price_market: float = 0.0
    exit_price_filled: float = 0.0
    exit_reason: str = ""
    exit_fee_usd: float = 0.0
    exit_slippage_usd: float = 0.0
    realized_pnl_usd: float = 0.0
    realized_pnl_pct: float = 0.0


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


_ADDITIVE_MIGRATIONS: list[tuple[str, str, str]] = [
    ("wallet", "watched_at", "TIMESTAMP"),
    ("wallet", "last_seen_sig", "VARCHAR"),
    ("walletstat", "avg_trade_size_usd", "FLOAT DEFAULT 0"),
    ("walletstat", "avg_hold_minutes", "FLOAT DEFAULT 0"),
    ("walletstat", "trades_per_day", "FLOAT DEFAULT 0"),
    ("walletstat", "is_likely_bot", "BOOLEAN DEFAULT 0"),
]


async def _apply_additive_migrations(conn) -> None:
    for table, col, typ in _ADDITIVE_MIGRATIONS:
        res = await conn.execute(text(f"PRAGMA table_info({table})"))
        cols = {row[1] for row in res.fetchall()}
        if col not in cols:
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {typ}"))


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
        await _apply_additive_migrations(conn)


async def session() -> AsyncSession:
    return SessionLocal()
