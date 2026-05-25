"""GeckoTerminal OHLCV fetcher + helpers to locate the right pool for a mint.

API: https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool}/ohlcv/{tf}
  ?aggregate={n}&before_timestamp={unix}&limit={1..1000}
Returns ohlcv_list as [[ts, open, high, low, close, vol_usd], ...] (newest first).

Free, no auth. ~30 req/min rate limit per IP.
"""

import re
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

GT_BASE = "https://api.geckoterminal.com/api/v2"

DEX_PAIR_RE = re.compile(
    r"dexscreener\.com/solana/([1-9A-HJ-NP-Za-km-z]{32,44})", re.IGNORECASE
)


def pool_from_dex_url(url: str) -> str | None:
    if not url:
        return None
    m = DEX_PAIR_RE.search(url)
    return m.group(1) if m else None


class OhlcvClient:
    def __init__(self, timeout: float = 12.0) -> None:
        self.timeout = timeout

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    async def ohlcv(
        self,
        pool: str,
        *,
        timeframe: str = "minute",
        aggregate: int = 5,
        before_ts: int | None = None,
        limit: int = 1000,
    ) -> list[list[float]]:
        url = f"{GT_BASE}/networks/solana/pools/{pool}/ohlcv/{timeframe}"
        params: dict[str, Any] = {"aggregate": aggregate, "limit": limit}
        if before_ts is not None:
            params["before_timestamp"] = before_ts
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.get(url, params=params)
            if r.status_code == 404:
                return []
            r.raise_for_status()
            data = r.json()
        rows = (((data.get("data") or {}).get("attributes") or {}).get("ohlcv_list")) or []
        rows.sort(key=lambda b: b[0])
        return rows

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=4))
    async def find_pool_for_mint(self, mint: str) -> str | None:
        """Pick the highest-liquidity Solana pool for a mint via GeckoTerminal."""
        url = f"{GT_BASE}/networks/solana/tokens/{mint}/pools"
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.get(url, params={"page": 1})
            if r.status_code in (404, 422):
                return None
            r.raise_for_status()
            data = r.json()
        pools = data.get("data") or []
        if not pools:
            return None

        def reserve(p: dict) -> float:
            try:
                return float((p.get("attributes") or {}).get("reserve_in_usd") or 0.0)
            except Exception:
                return 0.0

        pools.sort(key=reserve, reverse=True)
        addr = ((pools[0].get("attributes") or {}).get("address")) or ""
        return addr or None
