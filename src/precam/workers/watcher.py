"""Real-time watcher for top-ranked smart wallets.

For each `is_watched=True` Wallet, poll Helius enhanced SWAP txs every
`WATCHER_INTERVAL` seconds. Dedup via `last_seen_sig`. For each new BUY
above `WATCHER_MIN_BUY_USD`, count how many watched wallets bought the
same mint within the convergence window, enrich with DexScreener, and
push a Telegram alert.
"""

import asyncio
import html as _html
from datetime import datetime, timedelta
from typing import Iterable

from loguru import logger
from sqlalchemy import distinct, func, select

from ..analytics.swaps import QUOTE_MINTS, parse_swap
from ..config import settings
from ..db import SessionLocal, Trade, Wallet
from ..notifier import telegram
from ..solana.dexscreener import DexScreenerClient
from ..solana.helius import HeliusClient


async def _sol_usd(dex: DexScreenerClient) -> float:
    meta = await dex.get_token("So11111111111111111111111111111111111111112")
    return float((meta or {}).get("price_usd") or 0.0) or 150.0


def _fmt_usd(v: float) -> str:
    if v >= 1_000_000:
        return f"${v/1_000_000:.2f}M"
    if v >= 1_000:
        return f"${v/1_000:.1f}K"
    if v >= 1:
        return f"${v:.2f}"
    if v > 0:
        return f"${v:.6f}".rstrip("0").rstrip(".")
    return "$0"


def _fmt_age(min_age: float) -> str:
    if min_age < 60:
        return f"{min_age:.0f}m"
    if min_age < 24 * 60:
        return f"{min_age/60:.1f}h"
    return f"{min_age/1440:.1f}d"


def _format_buy_alert(*, wallet: Wallet, trade: dict, meta: dict | None, convergence: int) -> str:
    name = _html.escape((meta or {}).get("name") or "?")
    symbol = _html.escape(((meta or {}).get("symbol") or "?").lstrip("$"))
    mint = trade["mint"]
    addr = wallet.address
    label = _html.escape(wallet.label or "")

    head = "🔥 <b>CONVERGENCE</b> " if convergence >= 2 else "🎯 <b>SMART BUY</b>"
    conv_line = ""
    if convergence >= 2:
        conv_line = (
            f"\n⚡ <b>{convergence}× watched wallets</b> bought "
            f"{symbol} in last {settings.watcher_convergence_window_min}m"
        )

    enrich = ""
    if meta:
        liq = float(meta.get("liquidity_usd") or 0.0)
        pair_created = meta.get("pair_created_at")
        age_str = "?"
        if pair_created:
            delta_min = max((datetime.utcnow() - pair_created.replace(tzinfo=None)).total_seconds() / 60, 0)
            age_str = _fmt_age(delta_min)
        enrich = (
            f"\nliq {_fmt_usd(liq)}  "
            f"px {_fmt_usd(float(meta.get('price_usd') or 0))}  "
            f"age {age_str}"
        )

    return (
        f"{head} — <b>{name}</b> (${symbol}){conv_line}\n"
        f"wallet <code>{addr[:6]}…{addr[-4:]}</code>  {label[:40]}\n"
        f"size <b>{trade['amount_token']:.2f}</b> tokens ≈ "
        f"<b>{_fmt_usd(trade['amount_usd'])}</b> @ {_fmt_usd(trade['price_usd'])}"
        f"{enrich}\n\n"
        f"<code>{mint}</code>\n"
        f'<a href="https://dexscreener.com/solana/{mint}">DexScreener</a> · '
        f'<a href="https://pump.fun/coin/{mint}">pump</a> · '
        f'<a href="https://gmgn.ai/sol/token/{mint}">GMGN</a> · '
        f'<a href="https://solscan.io/tx/{trade["tx_sig"]}">tx</a> · '
        f'<a href="https://gmgn.ai/sol/address/{addr}">wallet</a>'
    )


async def _convergence_count(
    session, mint: str, watched_addrs: set[str], window_min: int
) -> int:
    if not watched_addrs:
        return 0
    cutoff = datetime.utcnow() - timedelta(minutes=window_min)
    q = (
        select(func.count(distinct(Trade.wallet)))
        .where(Trade.mint == mint)
        .where(Trade.side == "buy")
        .where(Trade.wallet.in_(list(watched_addrs)))
        .where(Trade.block_time >= cutoff)
    )
    res = await session.execute(q)
    return int(res.scalar_one() or 0)


async def _poll_wallet(
    wallet: Wallet,
    helius: HeliusClient,
    dex: DexScreenerClient,
    sol_usd: float,
    watched_addrs: set[str],
) -> int:
    """Return number of new buy alerts sent for this wallet."""
    try:
        txs = await helius.enhanced_transactions(wallet.address, type_="SWAP", limit=20)
    except Exception as e:
        logger.warning(f"{wallet.address[:6]}.. helius fetch failed: {e}")
        return 0
    if not txs:
        return 0

    new_txs: list[dict] = []
    for tx in txs:
        if tx.get("signature") == wallet.last_seen_sig:
            break
        new_txs.append(tx)
    if not new_txs:
        return 0

    latest_sig = new_txs[0].get("signature")

    new_trades: list[dict] = []
    for tx in reversed(new_txs):
        new_trades.extend(parse_swap(tx, sol_usd=sol_usd))

    alerts_sent = 0
    async with SessionLocal() as s:
        for t in new_trades:
            if t["mint"] in QUOTE_MINTS:
                continue
            existing = await s.execute(
                select(Trade).where(
                    Trade.tx_sig == t["tx_sig"],
                    Trade.wallet == t["wallet"],
                    Trade.mint == t["mint"],
                    Trade.side == t["side"],
                )
            )
            if existing.scalar_one_or_none():
                continue
            s.add(Trade(**t))

            if t["side"] == "buy" and t["amount_usd"] >= settings.watcher_min_buy_usd:
                await s.flush()
                conv = await _convergence_count(
                    s, t["mint"], watched_addrs, settings.watcher_convergence_window_min
                )
                meta = None
                try:
                    meta = await dex.get_token(t["mint"])
                except Exception:
                    pass
                msg = _format_buy_alert(
                    wallet=wallet, trade=t, meta=meta, convergence=conv
                )
                if await telegram.send(msg):
                    alerts_sent += 1
                    logger.success(
                        f"alert: @{wallet.address[:6]} bought "
                        f"{(meta or {}).get('symbol') or t['mint'][:6]} "
                        f"${t['amount_usd']:.0f} (conv={conv})"
                    )

        res = await s.execute(select(Wallet).where(Wallet.address == wallet.address))
        w_db = res.scalar_one_or_none()
        if w_db and latest_sig:
            w_db.last_seen_sig = latest_sig
            s.add(w_db)
        await s.commit()

    return alerts_sent


async def watch_once() -> int:
    if not settings.helius_api_key:
        logger.error("HELIUS_API_KEY required for watcher")
        return 0

    helius = HeliusClient()
    dex = DexScreenerClient()
    sol_usd = await _sol_usd(dex)

    async with SessionLocal() as s:
        res = await s.execute(select(Wallet).where(Wallet.is_watched == True))  # noqa: E712
        watched = list(res.scalars().all())
    if not watched:
        logger.info("no watched wallets — use `precam wallet watch <addr>` or `wallet auto-watch`")
        return 0

    watched_addrs = {w.address for w in watched}
    total = 0
    for w in watched:
        total += await _poll_wallet(w, helius, dex, sol_usd, watched_addrs)
        await asyncio.sleep(0.3)
    return total


async def watch_forever() -> None:
    logger.info(
        f"watcher started: interval={settings.watcher_interval}s "
        f"min_buy=${settings.watcher_min_buy_usd:.0f} "
        f"conv_window={settings.watcher_convergence_window_min}m"
    )
    while True:
        try:
            await watch_once()
        except Exception as e:
            logger.exception(f"watcher loop error: {e}")
        await asyncio.sleep(settings.watcher_interval)


async def auto_watch_top(
    *, top: int = 20, min_closed: int = 5, min_expectancy: float = 5.0
) -> tuple[int, int]:
    """Promote top-N wallets (by expectancy) to is_watched=True.

    Returns (newly_watched, total_watched).
    """
    from ..db import WalletStat
    async with SessionLocal() as s:
        q = (
            select(WalletStat)
            .where(WalletStat.closed_positions >= min_closed)
            .where(WalletStat.expectancy >= min_expectancy)
        )
        stats = list((await s.execute(q)).scalars().all())
        stats.sort(key=lambda r: r.expectancy, reverse=True)
        picks = stats[:top]

        added = 0
        for st in picks:
            wres = await s.execute(select(Wallet).where(Wallet.address == st.wallet))
            w = wres.scalar_one_or_none()
            if w is None or w.is_watched:
                continue
            w.is_watched = True
            w.watched_at = datetime.utcnow()
            s.add(w)
            added += 1
        await s.commit()

        total = (
            await s.execute(
                select(func.count(Wallet.address)).where(Wallet.is_watched == True)  # noqa: E712
            )
        ).scalar_one()
    return added, int(total)
