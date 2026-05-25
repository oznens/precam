"""Process a batch of Helius-enhanced transactions received via webhook."""

from typing import Any

from loguru import logger
from sqlalchemy import select

from ..analytics.swaps import parse_swap
from ..db import SessionLocal, Wallet
from ..solana.dexscreener import DexScreenerClient
from .watcher import _sol_usd, process_trades


async def handle_helius_batch(payload: list[dict[str, Any]]) -> dict[str, int]:
    """Entry point for the FastAPI webhook route. Returns counts for logging."""
    if not payload:
        return {"received": 0, "trades": 0, "alerts": 0}

    async with SessionLocal() as s:
        watched = list(
            (
                await s.execute(select(Wallet).where(Wallet.is_watched == True))  # noqa: E712
            ).scalars().all()
        )
    if not watched:
        logger.warning("webhook received but no wallets are is_watched=True")
        return {"received": len(payload), "trades": 0, "alerts": 0}

    wallet_by_addr = {w.address: w for w in watched}
    watched_addrs = set(wallet_by_addr.keys())

    dex = DexScreenerClient()
    sol_usd = await _sol_usd(dex)

    new_trades: list[dict] = []
    for tx in payload:
        for t in parse_swap(tx, sol_usd=sol_usd):
            if t["wallet"] in watched_addrs:
                new_trades.append(t)

    if not new_trades:
        return {"received": len(payload), "trades": 0, "alerts": 0}

    alerts = await process_trades(
        new_trades=new_trades,
        wallet_by_addr=wallet_by_addr,
        watched_addrs=watched_addrs,
        dex=dex,
    )
    return {"received": len(payload), "trades": len(new_trades), "alerts": alerts}
