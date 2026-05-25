"""Batch-simulate stored Signals and watched-wallet BUY Trades with one strategy."""

import asyncio
import statistics
from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import select

from ..backtest.engine import TPSL, SimResult, simulate
from ..backtest.pricing import OhlcvClient, pool_from_dex_url
from ..db import (
    BacktestRun,
    BacktestTrade,
    SessionLocal,
    Signal,
    Trade,
    Wallet,
)


async def _bars_for_signal(signal: Signal, oc: OhlcvClient, entry_ts: datetime, span_min: int) -> list[list[float]]:
    pool = pool_from_dex_url(signal.dex_url)
    if not pool:
        pool = await oc.find_pool_for_mint(signal.mint)
    if not pool:
        return []
    end_ts = entry_ts + timedelta(minutes=span_min + 30)
    return await oc.ohlcv(
        pool,
        timeframe="minute",
        aggregate=5,
        before_ts=int(end_ts.timestamp()),
        limit=int(span_min / 5) + 50,
    )


async def _bars_for_mint(mint: str, oc: OhlcvClient, entry_ts: datetime, span_min: int) -> list[list[float]]:
    pool = await oc.find_pool_for_mint(mint)
    if not pool:
        return []
    end_ts = entry_ts + timedelta(minutes=span_min + 30)
    return await oc.ohlcv(
        pool,
        timeframe="minute",
        aggregate=5,
        before_ts=int(end_ts.timestamp()),
        limit=int(span_min / 5) + 50,
    )


def _make_trade(
    run_id: int, *, source_key: str, source_ref: str, mint: str, symbol: str,
    entry_ts: datetime, entry_price: float, result: SimResult,
) -> BacktestTrade:
    return BacktestTrade(
        run_id=run_id,
        source_key=source_key,
        source_ref=source_ref,
        mint=mint,
        symbol=symbol,
        entry_ts=entry_ts,
        entry_price=entry_price,
        exit_ts=result.exit_ts,
        exit_price=result.exit_price,
        exit_reason=result.exit_reason,
        pnl_pct=result.pnl_pct,
        hold_minutes=result.hold_minutes,
        max_favourable_pct=result.max_favourable_pct,
        max_adverse_pct=result.max_adverse_pct,
    )


def _aggregate(run: BacktestRun, results: list[BacktestTrade]) -> None:
    run.total_trades = len(results)
    closed = [r for r in results if r.exit_reason in ("tp", "sl", "timeout")]
    run.closed_trades = len(closed)
    if not closed:
        return
    wins = [r for r in closed if r.pnl_pct > 0]
    losses = [r for r in closed if r.pnl_pct <= 0]
    run.wins = len(wins)
    run.losses = len(losses)
    run.win_rate = round(len(wins) / len(closed), 4)
    pnls = [r.pnl_pct for r in closed]
    run.avg_pnl_pct = round(sum(pnls) / len(pnls), 2)
    run.median_pnl_pct = round(statistics.median(pnls), 2)
    run.sum_pnl_pct = round(sum(pnls), 2)
    avg_win = (sum(r.pnl_pct for r in wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(r.pnl_pct for r in losses) / len(losses)) if losses else 0.0
    run.expectancy = round(
        run.win_rate * avg_win + (1 - run.win_rate) * avg_loss, 2
    )


async def run_backtest(
    *,
    name: str,
    source: str,
    tp_pct: float = 100.0,
    sl_pct: float = -30.0,
    max_hold_min: int = 720,
    limit: int = 100,
    concurrency: int = 3,
) -> int:
    strat = TPSL(tp_pct=tp_pct, sl_pct=sl_pct, max_hold_min=max_hold_min)
    oc = OhlcvClient()

    async with SessionLocal() as s:
        run = BacktestRun(
            name=name,
            source=source,
            strategy="tp_sl_timeout",
            tp_pct=tp_pct,
            sl_pct=sl_pct,
            max_hold_min=max_hold_min,
        )
        s.add(run)
        await s.commit()
        await s.refresh(run)
        run_id = run.id

    sem = asyncio.Semaphore(concurrency)
    results: list[BacktestTrade] = []

    if source == "signal":
        async with SessionLocal() as s:
            q = (
                select(Signal)
                .where(Signal.price_usd > 0)
                .order_by(Signal.created_at.desc())
                .limit(limit)
            )
            items = list((await s.execute(q)).scalars().all())
        logger.info(f"backtest signals: {len(items)} candidates")

        async def _one_sig(sig: Signal):
            async with sem:
                bars = await _bars_for_signal(sig, oc, sig.created_at, max_hold_min)
                if not bars:
                    return None
                res = simulate(
                    entry_price=sig.price_usd, entry_ts=sig.created_at, bars=bars, strategy=strat,
                )
                if res is None:
                    return None
                return _make_trade(
                    run_id,
                    source_key=sig.handle,
                    source_ref=str(sig.id),
                    mint=sig.mint, symbol=sig.symbol,
                    entry_ts=sig.created_at, entry_price=sig.price_usd, result=res,
                )

        gathered = await asyncio.gather(*(_one_sig(s_) for s_ in items))
        results = [r for r in gathered if r is not None]

    elif source == "wallet":
        async with SessionLocal() as s:
            watched = (
                await s.execute(select(Wallet).where(Wallet.is_watched == True))  # noqa: E712
            ).scalars().all()
            watched_addrs = [w.address for w in watched]
            q = (
                select(Trade)
                .where(Trade.side == "buy")
                .where(Trade.price_usd > 0)
                .where(Trade.wallet.in_(watched_addrs))
                .order_by(Trade.block_time.desc())
                .limit(limit)
            )
            items = list((await s.execute(q)).scalars().all())
        logger.info(f"backtest wallet buys: {len(items)} candidates ({len(watched_addrs)} watched wallets)")

        async def _one_tr(tr: Trade):
            async with sem:
                bars = await _bars_for_mint(tr.mint, oc, tr.block_time, max_hold_min)
                if not bars:
                    return None
                res = simulate(
                    entry_price=tr.price_usd, entry_ts=tr.block_time, bars=bars, strategy=strat,
                )
                if res is None:
                    return None
                return _make_trade(
                    run_id,
                    source_key=tr.wallet,
                    source_ref=tr.tx_sig,
                    mint=tr.mint, symbol="",
                    entry_ts=tr.block_time, entry_price=tr.price_usd, result=res,
                )

        gathered = await asyncio.gather(*(_one_tr(t) for t in items))
        results = [r for r in gathered if r is not None]
    else:
        raise ValueError(f"unknown source: {source} (use 'signal' or 'wallet')")

    async with SessionLocal() as s:
        for r in results:
            s.add(r)
        run = (await s.execute(select(BacktestRun).where(BacktestRun.id == run_id))).scalar_one()
        _aggregate(run, results)
        run.finished_at = datetime.utcnow()
        s.add(run)
        await s.commit()

    logger.success(
        f"run #{run_id} '{name}': {run.total_trades} trades, "
        f"closed={run.closed_trades} win_rate={run.win_rate*100:.0f}% "
        f"exp={run.expectancy:+.1f}% avg={run.avg_pnl_pct:+.1f}%"
    )
    return run_id


async def leaderboard(run_id: int, *, min_trades: int = 3) -> list[dict]:
    """Aggregate per-source-key stats for a given run."""
    async with SessionLocal() as s:
        rows = (
            await s.execute(
                select(BacktestTrade).where(BacktestTrade.run_id == run_id)
            )
        ).scalars().all()

    groups: dict[str, list[BacktestTrade]] = {}
    for r in rows:
        groups.setdefault(r.source_key, []).append(r)

    out = []
    for key, items in groups.items():
        closed = [i for i in items if i.exit_reason in ("tp", "sl", "timeout")]
        if len(closed) < min_trades:
            continue
        wins = [i for i in closed if i.pnl_pct > 0]
        pnls = [i.pnl_pct for i in closed]
        avg_win = sum(i.pnl_pct for i in wins) / len(wins) if wins else 0.0
        avg_loss = sum(i.pnl_pct for i in closed if i.pnl_pct <= 0) / max(
            len(closed) - len(wins), 1
        )
        win_rate = len(wins) / len(closed)
        out.append(
            {
                "source_key": key,
                "trades": len(items),
                "closed": len(closed),
                "wins": len(wins),
                "win_rate": round(win_rate, 4),
                "avg_pnl_pct": round(sum(pnls) / len(pnls), 2),
                "median_pnl_pct": round(statistics.median(pnls), 2),
                "expectancy": round(win_rate * avg_win + (1 - win_rate) * avg_loss, 2),
                "sum_pnl_pct": round(sum(pnls), 2),
            }
        )
    out.sort(key=lambda x: x["expectancy"], reverse=True)
    return out
