from pathlib import Path

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from loguru import logger
from sqlalchemy import func, select

from .config import settings
from .workers.webhook_handler import handle_helius_batch

from .db import (
    BacktestRun,
    BacktestTrade,
    Kol,
    PaperPortfolio,
    PaperPosition,
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
    show_bots: int = 0,
):
    async with SessionLocal() as s:
        q = select(WalletStat).where(WalletStat.closed_positions >= min_closed)
        if not show_bots:
            q = q.where(WalletStat.is_likely_bot == False)  # noqa: E712
        res = await s.execute(q)
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
        bots_total = (
            await s.execute(
                select(func.count(WalletStat.wallet)).where(
                    WalletStat.is_likely_bot == True  # noqa: E712
                )
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
            "show_bots": bool(show_bots),
            "bots_total": bots_total,
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


@app.get("/paper", response_class=HTMLResponse)
async def paper_page(request: Request):
    async with SessionLocal() as s:
        portfolio = (await s.execute(select(PaperPortfolio))).scalar_one_or_none()
        opens = list(
            (
                await s.execute(
                    select(PaperPosition)
                    .where(PaperPosition.status == "open")
                    .order_by(PaperPosition.opened_at.desc())
                )
            ).scalars().all()
        )
        closed = list(
            (
                await s.execute(
                    select(PaperPosition)
                    .where(PaperPosition.status == "closed")
                    .order_by(PaperPosition.closed_at.desc())
                    .limit(100)
                )
            ).scalars().all()
        )

    leaderboard = []
    if closed:
        groups: dict[str, list] = {}
        for p in closed:
            groups.setdefault(p.source_key or p.source_kind, []).append(p)
        for k, items in groups.items():
            if len(items) < 2:
                continue
            wins = [p for p in items if p.realized_pnl_usd > 0]
            n = len(items)
            pnls_pct = [p.realized_pnl_pct for p in items]
            avg_win = (
                sum(p.realized_pnl_pct for p in wins) / len(wins) if wins else 0.0
            )
            avg_loss = (
                sum(p.realized_pnl_pct for p in items if p.realized_pnl_pct <= 0)
                / max(n - len(wins), 1)
            )
            win_rate = len(wins) / n
            leaderboard.append({
                "key": k, "n": n, "wins": len(wins),
                "win_rate": win_rate,
                "avg_pnl_pct": sum(pnls_pct) / n,
                "expectancy": win_rate * avg_win + (1 - win_rate) * avg_loss,
                "sum_usd": sum(p.realized_pnl_usd for p in items),
            })
        leaderboard.sort(key=lambda r: r["expectancy"], reverse=True)

    open_value = sum(o.last_price * o.entry_tokens for o in opens if o.last_price > 0)
    equity = (portfolio.current_cash_usd if portfolio else 0) + open_value
    roi = (
        (equity / portfolio.starting_balance_usd - 1) * 100
        if portfolio and portfolio.starting_balance_usd > 0
        else 0.0
    )

    return templates.TemplateResponse(
        request,
        "paper.html",
        {
            "p": portfolio,
            "opens": opens,
            "closed": closed,
            "leaderboard": leaderboard,
            "open_value": open_value,
            "equity": equity,
            "roi": roi,
        },
    )


@app.post("/webhooks/helius")
async def webhook_helius(
    request: Request,
    authorization: str | None = Header(default=None),
):
    """Helius enhanced-transactions webhook endpoint."""
    if settings.webhook_secret and authorization != settings.webhook_secret:
        raise HTTPException(status_code=401, detail="invalid auth header")
    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"bad json: {e}")
    if not isinstance(body, list):
        raise HTTPException(status_code=400, detail="expected a JSON array")
    try:
        result = await handle_helius_batch(body)
    except Exception as e:
        logger.exception(f"webhook processing failed: {e}")
        raise HTTPException(status_code=500, detail="processing error")
    logger.info(
        f"webhook: received={result['received']} "
        f"trades={result['trades']} alerts={result['alerts']}"
    )
    return result


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
