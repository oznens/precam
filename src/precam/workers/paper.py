"""Paper-trade simulator: live signals -> virtual portfolio with slippage + fees.

Two halves run each tick:
  1. open_new_positions() picks unprocessed Signal/Trade rows that pass the
     production alert filters and opens virtual positions (subject to
     portfolio capacity).
  2. manage_positions() marks every open position to the live DexScreener
     price, updates max-favourable/adverse, and closes on TP/SL/timeout.

Limitations vs. live trading:
  - Price polling is interval-based (default 60s). Spikes inside a polling
    window are missed (we'd never see them in the order book either if our
    bot ran on the same interval; document this for users).
  - Slippage is a flat percentage on each side; real impact depends on
    pool depth vs trade size. Override per-portfolio via `paper init`.
  - Fees are a flat USD amount per side (Solana net ~0.50). Adjust as the
    network changes.
"""

import asyncio
from datetime import datetime, timedelta
from typing import Iterable

from loguru import logger
from sqlalchemy import select

from ..config import settings
from ..db import (
    PaperPortfolio,
    PaperPosition,
    SessionLocal,
    Signal,
    Trade,
    Wallet,
)
from ..solana.dexscreener import DexScreenerClient


async def get_or_init_portfolio() -> PaperPortfolio:
    async with SessionLocal() as s:
        res = await s.execute(select(PaperPortfolio).where(PaperPortfolio.is_active == True))  # noqa: E712
        p = res.scalar_one_or_none()
        if p:
            return p
        p = PaperPortfolio(
            starting_balance_usd=settings.paper_starting_balance,
            current_cash_usd=settings.paper_starting_balance,
            position_size_usd=settings.paper_position_size_usd,
            max_concurrent=settings.paper_max_concurrent,
            slippage_pct=settings.paper_slippage_pct,
            fee_usd=settings.paper_fee_usd,
            tp_pct=settings.paper_tp_pct,
            sl_pct=settings.paper_sl_pct,
            max_hold_min=settings.paper_max_hold_min,
        )
        s.add(p)
        await s.commit()
        await s.refresh(p)
        return p


async def reset_portfolio(balance: float | None = None) -> PaperPortfolio:
    async with SessionLocal() as s:
        from sqlalchemy import delete
        await s.execute(delete(PaperPosition))
        old = (await s.execute(select(PaperPortfolio))).scalars().all()
        for o in old:
            await s.delete(o)
        bal = balance if balance is not None else settings.paper_starting_balance
        p = PaperPortfolio(
            starting_balance_usd=bal,
            current_cash_usd=bal,
            position_size_usd=settings.paper_position_size_usd,
            max_concurrent=settings.paper_max_concurrent,
            slippage_pct=settings.paper_slippage_pct,
            fee_usd=settings.paper_fee_usd,
            tp_pct=settings.paper_tp_pct,
            sl_pct=settings.paper_sl_pct,
            max_hold_min=settings.paper_max_hold_min,
        )
        s.add(p)
        await s.commit()
        await s.refresh(p)
        return p


async def _existing_refs(kind: str, refs: Iterable[str]) -> set[str]:
    refs = list(refs)
    if not refs:
        return set()
    async with SessionLocal() as s:
        res = await s.execute(
            select(PaperPosition.source_ref)
            .where(PaperPosition.source_kind == kind)
            .where(PaperPosition.source_ref.in_(refs))
        )
        return {r for (r,) in res.all()}


async def _open_position(
    portfolio: PaperPortfolio,
    *,
    kind: str,
    ref: str,
    source_key: str,
    mint: str,
    market_price: float,
    symbol: str,
    name: str,
) -> PaperPosition | None:
    if market_price <= 0:
        return None
    size = min(portfolio.position_size_usd, portfolio.current_cash_usd - portfolio.fee_usd)
    if size <= 0:
        return None

    entry_fill = market_price * (1 + portfolio.slippage_pct / 100.0)
    tokens = size / entry_fill
    slippage_cost = size * (portfolio.slippage_pct / 100.0)
    fee_cost = portfolio.fee_usd

    pos = PaperPosition(
        portfolio_id=portfolio.id,
        source_kind=kind,
        source_ref=ref,
        source_key=source_key,
        mint=mint,
        symbol=symbol,
        name=name,
        entry_price_market=market_price,
        entry_price_filled=entry_fill,
        entry_amount_usd=size,
        entry_tokens=tokens,
        entry_fee_usd=fee_cost,
        entry_slippage_usd=slippage_cost,
        tp_pct=portfolio.tp_pct,
        sl_pct=portfolio.sl_pct,
        max_hold_min=portfolio.max_hold_min,
        last_price=market_price,
        last_checked_at=datetime.utcnow(),
    )

    async with SessionLocal() as s:
        s.add(pos)
        p_db = (
            await s.execute(select(PaperPortfolio).where(PaperPortfolio.id == portfolio.id))
        ).scalar_one()
        p_db.current_cash_usd -= size + fee_cost
        p_db.total_fees_usd += fee_cost
        p_db.total_slippage_usd += slippage_cost
        p_db.positions_opened += 1
        p_db.updated_at = datetime.utcnow()
        s.add(p_db)
        await s.commit()
    logger.success(
        f"opened paper #{pos.id}: {symbol or mint[:6]} via {kind}:{source_key} "
        f"size=${size:.2f} @ ${entry_fill:.6f}"
    )
    return pos


async def open_new_positions() -> int:
    portfolio = await get_or_init_portfolio()
    async with SessionLocal() as s:
        open_count = (
            await s.execute(
                select(PaperPosition).where(
                    PaperPosition.portfolio_id == portfolio.id,
                    PaperPosition.status == "open",
                )
            )
        ).scalars().all()
        open_count = len(open_count)
    capacity = max(0, portfolio.max_concurrent - open_count)
    if capacity <= 0:
        return 0
    if portfolio.current_cash_usd < portfolio.position_size_usd:
        return 0

    dex = DexScreenerClient()
    opened = 0

    async with SessionLocal() as s:
        sig_res = await s.execute(
            select(Signal)
            .where(Signal.is_early == True)  # noqa: E712
            .where(Signal.liquidity_usd >= settings.min_liquidity_usd)
            .order_by(Signal.created_at.desc())
            .limit(capacity * 3)
        )
        sigs = list(sig_res.scalars().all())

    seen = await _existing_refs("signal", (str(s.id) for s in sigs))
    for sig in sigs:
        if opened >= capacity:
            break
        if str(sig.id) in seen:
            continue
        if portfolio.current_cash_usd < portfolio.position_size_usd + portfolio.fee_usd:
            break
        try:
            meta = await dex.get_token(sig.mint)
        except Exception as e:
            logger.warning(f"paper open: dex fetch failed for {sig.mint[:6]}: {e}")
            continue
        if not meta or float(meta.get("price_usd") or 0) <= 0:
            continue
        pos = await _open_position(
            portfolio,
            kind="signal",
            ref=str(sig.id),
            source_key=sig.handle,
            mint=sig.mint,
            market_price=float(meta["price_usd"]),
            symbol=meta.get("symbol") or sig.symbol,
            name=meta.get("name") or sig.name,
        )
        if pos:
            opened += 1
            portfolio = await get_or_init_portfolio()

    async with SessionLocal() as s:
        watched_addrs = [
            w.address
            for w in (
                await s.execute(select(Wallet).where(Wallet.is_watched == True))  # noqa: E712
            ).scalars().all()
        ]
        if watched_addrs and opened < capacity:
            tr_res = await s.execute(
                select(Trade)
                .where(Trade.side == "buy")
                .where(Trade.wallet.in_(watched_addrs))
                .where(Trade.amount_usd >= settings.watcher_min_buy_usd)
                .order_by(Trade.block_time.desc())
                .limit(capacity * 3)
            )
            trs = list(tr_res.scalars().all())
        else:
            trs = []

    seen_tr = await _existing_refs("wallet", (t.tx_sig for t in trs))
    for tr in trs:
        if opened >= capacity:
            break
        if tr.tx_sig in seen_tr:
            continue
        if portfolio.current_cash_usd < portfolio.position_size_usd + portfolio.fee_usd:
            break
        try:
            meta = await dex.get_token(tr.mint)
        except Exception as e:
            logger.warning(f"paper open: dex fetch failed for {tr.mint[:6]}: {e}")
            continue
        if not meta or float(meta.get("price_usd") or 0) <= 0:
            continue
        pos = await _open_position(
            portfolio,
            kind="wallet",
            ref=tr.tx_sig,
            source_key=tr.wallet,
            mint=tr.mint,
            market_price=float(meta["price_usd"]),
            symbol=meta.get("symbol") or "",
            name=meta.get("name") or "",
        )
        if pos:
            opened += 1
            portfolio = await get_or_init_portfolio()

    return opened


async def _close_position(
    pos: PaperPosition,
    *,
    market_price: float,
    reason: str,
    portfolio: PaperPortfolio,
) -> None:
    exit_fill = market_price * (1 - portfolio.slippage_pct / 100.0)
    proceeds = pos.entry_tokens * exit_fill
    slippage_cost = pos.entry_tokens * market_price * (portfolio.slippage_pct / 100.0)
    fee_cost = portfolio.fee_usd

    realized = proceeds - fee_cost - pos.entry_amount_usd
    realized_pct = (realized / pos.entry_amount_usd) * 100 if pos.entry_amount_usd > 0 else 0.0

    async with SessionLocal() as s:
        db_pos = (
            await s.execute(select(PaperPosition).where(PaperPosition.id == pos.id))
        ).scalar_one()
        db_pos.status = "closed"
        db_pos.closed_at = datetime.utcnow()
        db_pos.exit_price_market = market_price
        db_pos.exit_price_filled = exit_fill
        db_pos.exit_reason = reason
        db_pos.exit_fee_usd = fee_cost
        db_pos.exit_slippage_usd = slippage_cost
        db_pos.last_price = market_price
        db_pos.last_checked_at = datetime.utcnow()
        db_pos.realized_pnl_usd = round(realized, 4)
        db_pos.realized_pnl_pct = round(realized_pct, 2)
        s.add(db_pos)

        p_db = (
            await s.execute(select(PaperPortfolio).where(PaperPortfolio.id == portfolio.id))
        ).scalar_one()
        p_db.current_cash_usd += proceeds - fee_cost
        p_db.total_fees_usd += fee_cost
        p_db.total_slippage_usd += slippage_cost
        p_db.total_realized_pnl_usd += realized
        p_db.positions_closed += 1
        p_db.updated_at = datetime.utcnow()
        s.add(p_db)
        await s.commit()

    logger.success(
        f"closed paper #{pos.id} via {reason}: "
        f"entry ${pos.entry_price_filled:.6f} -> exit ${exit_fill:.6f} "
        f"pnl ${realized:+.2f} ({realized_pct:+.1f}%)"
    )


async def manage_positions() -> int:
    portfolio = await get_or_init_portfolio()
    async with SessionLocal() as s:
        opens = list(
            (
                await s.execute(
                    select(PaperPosition).where(
                        PaperPosition.portfolio_id == portfolio.id,
                        PaperPosition.status == "open",
                    )
                )
            ).scalars().all()
        )
    if not opens:
        return 0

    dex = DexScreenerClient()
    closed = 0
    now = datetime.utcnow()

    for pos in opens:
        try:
            meta = await dex.get_token(pos.mint)
        except Exception as e:
            logger.warning(f"paper manage: dex fetch failed for {pos.mint[:6]}: {e}")
            continue

        if not meta:
            held_min = (now - pos.opened_at).total_seconds() / 60
            if held_min >= pos.max_hold_min:
                await _close_position(
                    pos, market_price=pos.last_price or pos.entry_price_market,
                    reason="timeout_no_liquidity", portfolio=portfolio,
                )
                closed += 1
            continue

        market_price = float(meta.get("price_usd") or 0)
        if market_price <= 0:
            continue

        up_pct = (market_price / pos.entry_price_filled - 1) * 100
        if up_pct > pos.max_favourable_pct:
            pos.max_favourable_pct = round(up_pct, 2)
        if up_pct < pos.max_adverse_pct:
            pos.max_adverse_pct = round(up_pct, 2)

        held_min = (now - pos.opened_at).total_seconds() / 60
        reason = None
        if up_pct >= pos.tp_pct:
            reason = "tp"
        elif up_pct <= pos.sl_pct:
            reason = "sl"
        elif held_min >= pos.max_hold_min:
            reason = "timeout"

        if reason:
            await _close_position(
                pos, market_price=market_price, reason=reason, portfolio=portfolio,
            )
            closed += 1
        else:
            async with SessionLocal() as s:
                db_pos = (
                    await s.execute(select(PaperPosition).where(PaperPosition.id == pos.id))
                ).scalar_one()
                db_pos.last_price = market_price
                db_pos.last_checked_at = now
                db_pos.max_favourable_pct = pos.max_favourable_pct
                db_pos.max_adverse_pct = pos.max_adverse_pct
                s.add(db_pos)
                await s.commit()

    return closed


async def paper_loop(interval: int | None = None) -> None:
    iv = interval or settings.paper_interval
    portfolio = await get_or_init_portfolio()
    logger.info(
        f"paper loop started: balance=${portfolio.current_cash_usd:.2f} "
        f"size=${portfolio.position_size_usd:.2f} max={portfolio.max_concurrent} "
        f"tp={portfolio.tp_pct}% sl={portfolio.sl_pct}% hold={portfolio.max_hold_min}m "
        f"slip={portfolio.slippage_pct}% fee=${portfolio.fee_usd}"
    )
    while True:
        try:
            opened = await open_new_positions()
            closed = await manage_positions()
            if opened or closed:
                logger.info(f"paper tick: opened={opened} closed={closed}")
        except Exception as e:
            logger.exception(f"paper loop error: {e}")
        await asyncio.sleep(iv)
