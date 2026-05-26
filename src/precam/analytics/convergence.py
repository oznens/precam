"""Detect "convergence" — multiple watched wallets buying the same mint in a window.

Two independent watched wallets stepping into the same low-cap mint within a few
hours is a much stronger alpha signal than any single wallet's history can give.
This module is intentionally DB-only: it answers "which mints did the watchlist
agree on lately?" without spending a single Helius or Jupiter call.
"""

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select

from ..db import SessionLocal, Trade, Wallet
from .swaps import QUOTE_MINTS


async def find_convergence(
    window_hours: float = 24.0,
    min_wallets: int = 2,
    only_watched: bool = True,
) -> list[dict]:
    """Return mints bought by >= `min_wallets` distinct (watched) wallets in the window.

    Each row contains the mint, the wallets that bought it, total USD spent
    across all participants, the first and last buy timestamps, and the spread
    between them. Sorted by participant count desc, then tight spread first —
    a 3-wallet 20-minute convergence ranks above a 2-wallet 18-hour one.
    """
    cutoff = datetime.utcnow() - timedelta(hours=window_hours)

    async with SessionLocal() as s:
        watched_set: Optional[set[str]] = None
        if only_watched:
            res = await s.execute(select(Wallet.address).where(Wallet.is_watched.is_(True)))
            watched_set = {a for (a,) in res.all()}
            if not watched_set:
                return []

        q = select(Trade).where(
            Trade.side == "buy",
            Trade.block_time >= cutoff,
            Trade.mint.notin_(QUOTE_MINTS),
        )
        if watched_set is not None:
            q = q.where(Trade.wallet.in_(watched_set))
        res = await s.execute(q)
        trades = res.scalars().all()

    # Group by mint, keep per-wallet aggregates so we can report
    # "wallet X bought $400 at 14:02, wallet Y bought $1200 at 14:08".
    by_mint: dict[str, dict[str, dict]] = {}
    for t in trades:
        bucket = by_mint.setdefault(t.mint, {})
        w = bucket.setdefault(
            t.wallet,
            {"usd": 0.0, "first_ts": t.block_time, "last_ts": t.block_time},
        )
        w["usd"] += t.amount_usd or 0.0
        if t.block_time < w["first_ts"]:
            w["first_ts"] = t.block_time
        if t.block_time > w["last_ts"]:
            w["last_ts"] = t.block_time

    rows: list[dict] = []
    for mint, wallets in by_mint.items():
        if len(wallets) < min_wallets:
            continue
        firsts = [w["first_ts"] for w in wallets.values()]
        total_usd = sum(w["usd"] for w in wallets.values())
        spread = max(firsts) - min(firsts)
        rows.append(
            {
                "mint": mint,
                "n_wallets": len(wallets),
                "total_usd": total_usd,
                "first_buy": min(firsts),
                "last_buy": max(firsts),
                "spread_minutes": spread.total_seconds() / 60,
                "wallets": [
                    {"address": addr, "usd": w["usd"], "first_ts": w["first_ts"]}
                    for addr, w in sorted(wallets.items(), key=lambda kv: kv[1]["first_ts"])
                ],
            }
        )

    rows.sort(key=lambda r: (-r["n_wallets"], r["spread_minutes"]))
    return rows
