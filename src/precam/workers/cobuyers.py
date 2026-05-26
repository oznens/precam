"""Co-buyer discovery: which wallets keep showing up alongside our alpha seed.

Convergence (the DB-only detector) tells us when two *already-watched* wallets
overlap. Co-buyer discovery is the inverse: take ONE proven wallet, walk the
mints it recently bought, and ask "who else was buying these same mints around
the same time?" Wallets that surface across multiple of the seed's bets are
candidate alpha — they're either copy-traders riding the same source, or
peers in the same channel/group. Either way they're worth promoting onto the
watchlist so the convergence detector can corroborate them going forward.

GeckoTerminal's per-pool /trades endpoint returns ~300 most recent trades
including the trader's wallet address, so this is cheap: 1 API call per mint
to find the top pool, plus 1 call to fetch its trades.
"""

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from ..analytics.swaps import QUOTE_MINTS
from ..db import SessionLocal, Trade, Wallet
from ..solana.trending import GeckoTerminalClient


async def find_cobuyers(
    seed_wallet: str,
    days: float = 1.0,
    window_minutes: float = 30.0,
    min_overlap: int = 2,
    max_mints: int = 20,
) -> dict:
    """Return wallets that bought the same mints as `seed_wallet` within `window_minutes`.

    Process: pull the seed's recent buys from DB, find each mint's top pool,
    fetch that pool's recent trades from GeckoTerminal, then score every
    foreign wallet that bought within `window_minutes` of the seed's own buy.

    Returns a dict with seed metadata and a sorted list of candidate co-buyers
    (each with overlap count, total USD stake across overlaps, and the mints).
    """
    cutoff = datetime.utcnow() - timedelta(days=days)

    async with SessionLocal() as s:
        res = await s.execute(
            select(Trade).where(
                Trade.wallet == seed_wallet,
                Trade.side == "buy",
                Trade.block_time >= cutoff,
                Trade.mint.notin_(QUOTE_MINTS),
            )
        )
        seed_trades = res.scalars().all()
        watched_res = await s.execute(
            select(Wallet.address).where(Wallet.is_watched.is_(True))
        )
        watched = {a for (a,) in watched_res.all()}

    # Earliest buy timestamp per mint — anchor for the overlap window.
    seed_buy_at: dict[str, datetime] = {}
    for t in seed_trades:
        if t.mint not in seed_buy_at or t.block_time < seed_buy_at[t.mint]:
            seed_buy_at[t.mint] = t.block_time
    mints = list(seed_buy_at.keys())[:max_mints]

    if not mints:
        return {"seed": seed_wallet, "mints_scanned": 0, "candidates": []}

    gt = GeckoTerminalClient()
    # wallet -> {overlap_mints: set[mint], usd: float, hits: list[(mint, ts, usd, delta_s)]}
    by_wallet: dict[str, dict] = defaultdict(
        lambda: {"overlap_mints": set(), "usd": 0.0, "hits": []}
    )

    for mint in mints:
        anchor = seed_buy_at[mint]
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=timezone.utc)
        try:
            pool = await gt.top_pool_for_token(mint)
            if not pool:
                continue
            trades = await gt.pool_trades(pool)
        except Exception:
            continue
        window = timedelta(minutes=window_minutes)
        for tr in trades:
            if tr["kind"] != "buy" or not tr["wallet"] or not tr["block_time"]:
                continue
            if tr["wallet"] == seed_wallet or tr["wallet"] in watched:
                continue
            delta = abs((tr["block_time"] - anchor).total_seconds())
            if delta > window.total_seconds():
                continue
            w = by_wallet[tr["wallet"]]
            w["overlap_mints"].add(mint)
            w["usd"] += tr["volume_usd"]
            w["hits"].append(
                {
                    "mint": mint,
                    "ts": tr["block_time"],
                    "usd": tr["volume_usd"],
                    "delta_s": delta,
                }
            )

    candidates = []
    for addr, data in by_wallet.items():
        n = len(data["overlap_mints"])
        if n < min_overlap:
            continue
        candidates.append(
            {
                "address": addr,
                "n_overlap_mints": n,
                "total_usd": data["usd"],
                "mints": sorted(data["overlap_mints"]),
                "hits": sorted(data["hits"], key=lambda h: h["ts"]),
            }
        )
    candidates.sort(key=lambda c: (-c["n_overlap_mints"], -c["total_usd"]))

    return {
        "seed": seed_wallet,
        "mints_scanned": len(mints),
        "window_minutes": window_minutes,
        "candidates": candidates,
    }
