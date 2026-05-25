"""Wallet discovery + refresh + ranking jobs."""

import asyncio
from collections import Counter
from datetime import datetime

from loguru import logger
from sqlalchemy import delete, select

from ..analytics.positions import build_positions, compute_stats
from ..analytics.swaps import parse_swap
from ..config import settings
from ..db import Position, SessionLocal, Trade, Wallet, WalletStat
from ..solana.helius import HeliusClient
from ..solana.trending import GeckoTerminalClient


async def _get_sol_usd_estimate(helius: HeliusClient) -> float:
    """Quick estimate via Jupiter price proxy in DexScreener. Falls back to $150."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=6.0) as c:
            r = await c.get(
                "https://api.dexscreener.com/latest/dex/tokens/So11111111111111111111111111111111111111112"
            )
            r.raise_for_status()
            pairs = (r.json().get("pairs") or [])
            usd = [float(p.get("priceUsd") or 0) for p in pairs if float(p.get("priceUsd") or 0) > 0]
            if usd:
                usd.sort()
                return usd[len(usd) // 2]
    except Exception as e:
        logger.warning(f"sol/usd fetch failed: {e}")
    return 150.0


async def discover_from_trending(
    *,
    top_pools: int = 15,
    max_pages_per_pool: int = 3,
    min_buys_to_promote: int = 2,
    min_trade_size_usd: float = 200.0,
) -> int:
    """Walk trending pools -> parse swaps -> aggregate wallets that BOUGHT the base token.

    Only counts BUY trades whose amount_usd >= min_trade_size_usd to filter out
    sniper bots (which typically use $1-50 micro-trades). Wallets appearing in
    >= min_buys_to_promote trending pools are saved as candidates.
    Returns count of newly inserted wallets.
    """
    if not settings.helius_api_key:
        logger.error(
            "HELIUS_API_KEY not set — wallet discovery requires Helius enhanced txs. "
            "Get a free key at https://dashboard.helius.dev/ and add it to .env"
        )
        return 0

    gt = GeckoTerminalClient()
    helius = HeliusClient()
    sol_usd = await _get_sol_usd_estimate(helius)

    pages_needed = max(1, (top_pools + 19) // 20)
    pools = await gt.trending_pools(pages=pages_needed)
    pools = pools[:top_pools]
    if not pools:
        logger.warning("no trending pools returned")
        return 0
    logger.info(f"scanning {len(pools)} trending pools (sol/usd≈${sol_usd:.2f})")

    wallet_buys: Counter[str] = Counter()
    wallet_pool_set: dict[str, set[str]] = {}

    for pool in pools:
        pool_addr = pool["pool_address"]
        base_mint = pool["base_mint"]
        if not pool_addr or not base_mint:
            continue
        try:
            txs = await helius.iter_swaps(pool_addr, max_pages=max_pages_per_pool)
        except Exception as e:
            logger.warning(f"pool {pool_addr[:6]}: helius failed: {e}")
            continue

        buyers_here: set[str] = set()
        small_skipped = 0
        for tx in txs:
            trades = parse_swap(tx, sol_usd=sol_usd)
            for t in trades:
                if t["side"] != "buy" or t["mint"] != base_mint or not t["wallet"]:
                    continue
                if t["amount_usd"] < min_trade_size_usd:
                    small_skipped += 1
                    continue
                buyers_here.add(t["wallet"])
        logger.info(
            f"  {pool['base_symbol']:<14} {len(txs):>3} swap-tx -> {len(buyers_here)} buyers "
            f"(skipped {small_skipped} <${min_trade_size_usd:.0f})"
        )
        for w in buyers_here:
            wallet_buys[w] += 1
            wallet_pool_set.setdefault(w, set()).add(pool["base_symbol"])
        await asyncio.sleep(0.4)

    promoted = [(w, n) for w, n in wallet_buys.items() if n >= min_buys_to_promote]
    promoted.sort(key=lambda x: -x[1])
    logger.success(
        f"{len(promoted)} wallets seen in ≥{min_buys_to_promote} trending pools "
        f"(out of {len(wallet_buys)} total buyers)"
    )

    added = 0
    async with SessionLocal() as s:
        for addr, n in promoted:
            res = await s.execute(select(Wallet).where(Wallet.address == addr))
            if res.scalar_one_or_none():
                continue
            label = f"trending×{n}: {','.join(sorted(wallet_pool_set[addr]))[:80]}"
            s.add(
                Wallet(
                    address=addr,
                    label=label,
                    discovered_via="trending",
                    discovered_at=datetime.utcnow(),
                )
            )
            added += 1
        await s.commit()
    logger.success(f"{added} new wallet(s) added")
    return added


async def refresh_wallet(address: str, *, max_pages: int = 5) -> dict:
    """Pull a wallet's recent swaps, rebuild positions + stats, persist all."""
    if not settings.helius_api_key:
        raise RuntimeError(
            "HELIUS_API_KEY required for wallet refresh — set it in .env"
        )
    helius = HeliusClient()
    sol_usd = await _get_sol_usd_estimate(helius)
    txs = await helius.iter_swaps(address, max_pages=max_pages)

    all_trades: list[dict] = []
    for tx in txs:
        all_trades.extend(parse_swap(tx, sol_usd=sol_usd))

    positions = build_positions(all_trades)
    stats = compute_stats(positions, trades=all_trades)

    async with SessionLocal() as s:
        await s.execute(delete(Trade).where(Trade.wallet == address))
        await s.execute(delete(Position).where(Position.wallet == address))
        for t in all_trades:
            s.add(Trade(**t))
        for p in positions:
            s.add(Position(**p))

        res = await s.execute(select(WalletStat).where(WalletStat.wallet == address))
        existing = res.scalar_one_or_none()
        if existing:
            for k, v in stats.items():
                setattr(existing, k, v)
            s.add(existing)
        else:
            s.add(WalletStat(wallet=address, **stats))

        res = await s.execute(select(Wallet).where(Wallet.address == address))
        w = res.scalar_one_or_none()
        if w:
            w.last_refreshed_at = datetime.utcnow()
            s.add(w)
        await s.commit()

    bot_flag = " 🤖BOT" if stats.get("is_likely_bot") else ""
    logger.success(
        f"{address[:6]}.. trades={len(all_trades)} positions={len(positions)} "
        f"closed={stats['closed_positions']} win_rate={stats['win_rate']*100:.0f}% "
        f"expectancy={stats['expectancy']:+.1f}% pnl=${stats['total_realized_pnl_usd']:,.0f} "
        f"avg_size=${stats.get('avg_trade_size_usd', 0):.0f} "
        f"hold={stats.get('avg_hold_minutes', 0):.0f}m{bot_flag}"
    )
    return stats


async def refresh_all(*, limit: int | None = None, concurrency: int = 3) -> int:
    """Refresh every wallet in the DB (or first N), bounded by concurrency."""
    async with SessionLocal() as s:
        q = select(Wallet).order_by(Wallet.discovered_at.desc())
        if limit:
            q = q.limit(limit)
        wallets = list((await s.execute(q)).scalars().all())

    sem = asyncio.Semaphore(concurrency)
    done = 0

    async def _one(addr: str):
        nonlocal done
        async with sem:
            try:
                await refresh_wallet(addr)
            except Exception as e:
                logger.error(f"{addr[:6]}.. refresh failed: {e}")
            done += 1

    await asyncio.gather(*(_one(w.address) for w in wallets))
    return done
