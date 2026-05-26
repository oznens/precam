"""Jupiter Price API v3 — authoritative USD price for Solana mints.

Why this over DexScreener for mark-to-market: Jupiter aggregates across every
Solana DEX and produces a single price per mint that's resistant to inverted-
quote DLMM bugs (e.g. DexScreener will say JUP = $997 from a Meteora pool
where Jupiter correctly says $0.20). The endpoint also returns the mint's
aggregate `liquidity` and a `launchpad` field, which lets us tell at a glance
whether a token is alive (real liquidity) or a rug victim.

Batch shape: comma-separated mints in `ids`. Free `lite-api.jup.ag` host has
no documented per-second cap but we sit behind a token bucket anyway so a
busy audit doesn't accidentally hammer it.
"""

import asyncio
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ._ratelimit import AsyncRateLimiter

_JUP_LIMITER = AsyncRateLimiter(per_second=4.0)
_JUP_BASE = "https://lite-api.jup.ag/price/v3"
# Jupiter accepts comma-separated ids but the URL gets long fast; chunk to keep
# a single request under ~6KB.
_CHUNK = 50


class JupiterPriceClient:
    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    async def _fetch(self, mints: list[str]) -> dict[str, Any]:
        async with _JUP_LIMITER:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                r = await c.get(_JUP_BASE, params={"ids": ",".join(mints)})
                r.raise_for_status()
                return r.json() or {}

    async def prices(self, mints: list[str]) -> dict[str, dict[str, Any]]:
        """Return {mint: {usd_price, liquidity, decimals, launchpad?, ...}}.

        Mints Jupiter doesn't price are simply absent from the result.
        """
        unique = list({m for m in mints if m})
        if not unique:
            return {}
        out: dict[str, dict[str, Any]] = {}
        for start in range(0, len(unique), _CHUNK):
            chunk = unique[start : start + _CHUNK]
            data = await self._fetch(chunk)
            for mint, info in data.items():
                if isinstance(info, dict) and info.get("usdPrice") is not None:
                    out[mint] = {
                        "price_usd": float(info["usdPrice"]),
                        "liquidity_usd": float(info.get("liquidity") or 0.0),
                        "decimals": int(info.get("decimals") or 0),
                        "launchpad": info.get("launchpad") or "",
                        "price_change_24h": float(info.get("priceChange24h") or 0.0),
                    }
        return out
