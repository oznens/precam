from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from .db import (
    BacktestRun,
    BacktestTrade,
    Kol,
    Position,
    PumpToken,
    SessionLocal,
    Signal,
    Wallet,
    WalletStat,
    init_db,
)
from .workers.backtest import leaderboard as backtest_leaderboard

TEMPLATES_DIR = Path(__file__).parent / "templates"
app = FastAPI(title="precam dashboard")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@app.on_event("startup")
async def _startup() -> None:
    await init_db()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, early: int = 0, limit: int = 100):
    async with SessionLocal() as s:
        q = select(Signal).order_by(Signal.created_at.desc()).limit(limit)
        if early:
            q = (
                select(Signal)
                .where(Signal.is_early == True)  # noqa: E712
                .order_by(Signal.created_at.desc())
                .limit(limit)
            )
        res = await s.execute(q)
        signals = res.scalars().all()

        total = (await s.execute(select(func.count(Signal.id)))).scalar_one()
        early_count = (
            await s.execute(
                select(func.count(Signal.id)).where(Signal.is_early == True)  # noqa: E712
            )
        ).scalar_one()
        kols = (await s.execute(select(Kol).order_by(Kol.weight.desc()))).scalars().all()

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "signals": signals,
            "kols": kols,
            "total": total,
            "early_count": early_count,
            "early_only": bool(early),
        },
    )


@app.get("/wallets", response_class=HTMLResponse)
async def wallets_page(
    request: Request,
    by: str = "expectancy",
    min_closed: int = 5,
    limit: int = 100,
    watched_only: int = 0,
):
    async with SessionLocal() as s:
        res = await s.execute(
            select(WalletStat).where(WalletStat.closed_positions >= min_closed)
        )
        stats = list(res.scalars().all())
        wallets = {
            w.address: w
            for w in (await s.execute(select(Wallet))).scalars().all()
        }
        total_wallets = (await s.execute(select(func.count(Wallet.address)))).scalar_one()
        watched_count = (
            await s.execute(
                select(func.count(Wallet.address)).where(Wallet.is_watched == True)  # noqa: E712
            )
        ).scalar_one()

    if watched_only:
        stats = [st for st in stats if (wallets.get(st.wallet) and wallets[st.wallet].is_watched)]
    key = {
        "expectancy": lambda r: r.expectancy,
        "win_rate": lambda r: r.win_rate,
        "pnl": lambda r: r.total_realized_pnl_usd,
    }.get(by, lambda r: r.expectancy)
    stats.sort(key=key, reverse=True)
    rows = [
        {"stat": st, "wallet": wallets.get(st.wallet)}
        for st in stats[:limit]
    ]
    return templates.TemplateResponse(
        request,
        "wallets.html",
        {
            "rows": rows,
            "sort_by": by,
            "min_closed": min_closed,
            "total_wallets": total_wallets,
            "watched_count": watched_count,
            "watched_only": bool(watched_only),
            "qualified": len(stats),
        },
    )


@app.get("/wallet/{address}", response_class=HTMLResponse)
async def wallet_detail(request: Request, address: str):
    async with SessionLocal() as s:
        wallet = (
            await s.execute(select(Wallet).where(Wallet.address == address))
        ).scalar_one_or_none()
        stat = (
            await s.execute(select(WalletStat).where(WalletStat.wallet == address))
        ).scalar_one_or_none()
        positions = list(
            (
                await s.execute(
                    select(Position).where(Position.wallet == address).order_by(
                        Position.opened_at.desc()
                    )
                )
            )
            .scalars()
            .all()
        )
    return templates.TemplateResponse(
        request,
        "wallet_detail.html",
        {"wallet": wallet, "stat": stat, "positions": positions, "address": address},
    )


@app.get("/pump", response_class=HTMLResponse)
async def pump_page(
    request: Request,
    clean_only: int = 0,
    max_risk: float = 100.0,
    limit: int = 100,
):
    async with SessionLocal() as s:
        q = select(PumpToken).order_by(PumpToken.created_at.desc()).limit(limit * 3)
        res = await s.execute(q)
        tokens = list(res.scalars().all())
        total = (await s.execute(select(func.count(PumpToken.mint)))).scalar_one()
        clean_count = (
            await s.execute(
                select(func.count(PumpToken.mint)).where(PumpToken.is_clean == True)  # noqa: E712
            )
        ).scalar_one()
        scored_count = (
            await s.execute(
                select(func.count(PumpToken.mint)).where(PumpToken.last_checked_at.is_not(None))
            )
        ).scalar_one()

    if clean_only:
        tokens = [t for t in tokens if t.is_clean]
    tokens = [t for t in tokens if t.rug_risk <= max_risk][:limit]

    return templates.TemplateResponse(
        request,
        "pump.html",
        {
            "tokens": tokens,
            "total": total,
            "clean_count": clean_count,
            "scored_count": scored_count,
            "clean_only": bool(clean_only),
            "max_risk": max_risk,
        },
    )


@app.get("/backtest", response_class=HTMLResponse)
async def backtest_page(request: Request, limit: int = 50):
    async with SessionLocal() as s:
        runs = list(
            (
                await s.execute(
                    select(BacktestRun).order_by(BacktestRun.started_at.desc()).limit(limit)
                )
            ).scalars().all()
        )
    return templates.TemplateResponse(
        request, "backtest_runs.html", {"runs": runs}
    )


@app.get("/backtest/{run_id}", response_class=HTMLResponse)
async def backtest_run_page(request: Request, run_id: int, min_trades: int = 3):
    async with SessionLocal() as s:
        run = (
            await s.execute(select(BacktestRun).where(BacktestRun.id == run_id))
        ).scalar_one_or_none()
        trades = list(
            (
                await s.execute(
                    select(BacktestTrade)
                    .where(BacktestTrade.run_id == run_id)
                    .order_by(BacktestTrade.entry_ts.desc())
                    .limit(200)
                )
            ).scalars().all()
        )
    lb = await backtest_leaderboard(run_id, min_trades=min_trades)
    return templates.TemplateResponse(
        request,
        "backtest_run.html",
        {"run": run, "trades": trades, "leaderboard": lb, "min_trades": min_trades},
    )


@app.get("/api/signals")
async def api_signals(limit: int = 100, early: bool = False):
    async with SessionLocal() as s:
        q = select(Signal).order_by(Signal.created_at.desc()).limit(limit)
        if early:
            q = (
                select(Signal)
                .where(Signal.is_early == True)  # noqa: E712
                .order_by(Signal.created_at.desc())
                .limit(limit)
            )
        res = await s.execute(q)
        return [r.model_dump() for r in res.scalars().all()]
