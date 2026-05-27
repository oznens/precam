"""Recurring convergence detector: poll DB, alert on new alpha overlap.

The convergence detector itself is a pure DB query — cheap to run on a tight
loop. This worker wraps it in a polling cadence, dedupes against the
ConvergenceAlert table (so we only fire once per (mint, n_wallets) tuple),
and pulls a live Jupiter price for each hit so the alert carries enough
context to act on without opening another tool.

Re-alerting policy: a hit re-fires when its n_wallets count climbs. Going
from 2-wallet to 3-wallet convergence is a meaningfully stronger signal —
we want a new ping for it — but seeing the same 2-wallet hit on every
30-minute poll is noise we suppress with the composite primary key.
"""

import asyncio
from datetime import datetime

from loguru import logger
from sqlalchemy import select

from ..analytics.convergence import find_convergence
from ..db import ConvergenceAlert, SessionLocal
from ..notifier import telegram
from ..solana.jupiter import JupiterPriceClient


def _short(addr: str) -> str:
    return f"{addr[:4]}…{addr[-4:]}"


def _format_alert(hit: dict, price: dict | None) -> str:
    mint = hit["mint"]
    n = hit["n_wallets"]
    spread = hit["spread_minutes"]
    spread_s = f"{spread:.0f}m" if spread < 120 else f"{spread/60:.1f}h"
    price_line = ""
    if price:
        price_line = (
            f"price ${price['price_usd']:.6g}  "
            f"liq ${price['liquidity_usd']:,.0f}  "
            f"24h {price['price_change_24h']:+.1f}%\n"
        )
    wallet_lines = "\n".join(
        f"  {_short(w['address'])}  ${w['usd']:,.0f}  "
        f"{w['first_ts'].strftime('%H:%M:%S')}"
        for w in hit["wallets"]
    )
    return (
        f"🎯 <b>CONVERGENCE</b> — {n} alpha wallets in <b>{spread_s}</b>\n"
        f"{price_line}"
        f"total ${hit['total_usd']:,.0f}\n"
        f"<code>{mint}</code>\n"
        f"{wallet_lines}\n"
        f'<a href="https://gmgn.ai/sol/token/{mint}">GMGN</a> · '
        f'<a href="https://dexscreener.com/solana/{mint}">DexScreener</a>'
    )


async def convergence_scan_once(
    *,
    window_hours: float = 12.0,
    min_wallets: int = 2,
) -> list[dict]:
    """Run one scan; alert on hits we haven't seen at this n_wallets count yet.

    Order matters: we record the dedup row and commit BEFORE sending the
    telegram alert, so a slow/failing telegram call doesn't hold the DB
    transaction open across a network round-trip.
    """
    hits = await find_convergence(window_hours=window_hours, min_wallets=min_wallets)
    if not hits:
        return []

    async with SessionLocal() as s:
        res = await s.execute(select(ConvergenceAlert))
        seen: set[tuple[str, int]] = {(a.mint, a.n_wallets) for a in res.scalars().all()}
        new_hits = [h for h in hits if (h["mint"], h["n_wallets"]) not in seen]
        if not new_hits:
            return []
        for h in new_hits:
            s.add(
                ConvergenceAlert(
                    mint=h["mint"],
                    n_wallets=h["n_wallets"],
                    total_usd=h["total_usd"],
                )
            )
        await s.commit()

    jup = JupiterPriceClient()
    prices = await jup.prices([h["mint"] for h in new_hits])
    for h in new_hits:
        msg = _format_alert(h, prices.get(h["mint"]))
        logger.info(
            f"convergence alert: {h['mint'][:8]}.. "
            f"n={h['n_wallets']} ${h['total_usd']:,.0f}"
        )
        await telegram.send(msg)

    return new_hits


async def convergence_loop(
    *,
    interval_min: int = 30,
    window_hours: float = 12.0,
    min_wallets: int = 2,
) -> None:
    """Run convergence_scan_once forever on `interval_min` cadence."""
    logger.info(
        f"convergence loop: every {interval_min}m, "
        f"window={window_hours}h, min_wallets={min_wallets}"
    )
    while True:
        try:
            new = await convergence_scan_once(
                window_hours=window_hours, min_wallets=min_wallets
            )
            logger.info(
                f"convergence scan at {datetime.utcnow().isoformat()}: "
                f"{len(new)} new alert(s)"
            )
        except Exception as e:
            logger.exception(f"convergence scan failed: {e}")
        await asyncio.sleep(interval_min * 60)
