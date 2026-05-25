from datetime import datetime, timezone
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import settings


class DexScreenerClient:
    def __init__(self, base: str | None = None, timeout: float = 10.0) -> None:
        self.base = (base or settings.dexscreener_base).rstrip("/")
        self.timeout = timeout

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    async def get_token(self, mint: str) -> dict[str, Any] | None:
        url = f"{self.base}/latest/dex/tokens/{mint}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.get(url)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            data = r.json()
        pairs = data.get("pairs") or []
        sol_pairs = [p for p in pairs if p.get("chainId") == "solana"]
        if not sol_pairs:
            return None
        best = max(sol_pairs, key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0.0))
        return _shape(best, mint)


def _shape(p: dict[str, Any], mint: str) -> dict[str, Any]:
    liq = (p.get("liquidity") or {}).get("usd") or 0.0
    created_ms = p.get("pairCreatedAt")
    created_at = (
        datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc) if created_ms else None
    )
    base_token = p.get("baseToken") or {}
    return {
        "mint": mint,
        "name": base_token.get("name") or "",
        "symbol": base_token.get("symbol") or "",
        "price_usd": float(p.get("priceUsd") or 0.0),
        "liquidity_usd": float(liq),
        "fdv_usd": float(p.get("fdv") or 0.0),
        "volume_h24": float((p.get("volume") or {}).get("h24") or 0.0),
        "tx_h24_buys": int((p.get("txns") or {}).get("h24", {}).get("buys") or 0),
        "tx_h24_sells": int((p.get("txns") or {}).get("h24", {}).get("sells") or 0),
        "pair_created_at": created_at,
        "dex_url": p.get("url") or f"https://dexscreener.com/solana/{mint}",
        "dex_id": p.get("dexId") or "",
    }
