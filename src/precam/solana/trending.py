from datetime import datetime, timezone
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

BASE = "https://api.geckoterminal.com/api/v2"


class GeckoTerminalClient:
    """Keyless GeckoTerminal API. Used to seed wallet discovery with trending pools."""

    def __init__(self, timeout: float = 12.0) -> None:
        self.timeout = timeout

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    async def _get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.get(f"{BASE}{path}", params=params, headers={"accept": "application/json"})
            r.raise_for_status()
            return r.json()

    async def trending_pools(self, page: int = 1, duration: str = "24h") -> list[dict[str, Any]]:
        """Return Solana trending pools. duration in {5m, 1h, 6h, 24h}."""
        data = await self._get(
            "/networks/solana/trending_pools",
            params={"page": page, "duration": duration},
        )
        return [_shape_pool(p) for p in (data.get("data") or [])]

    async def top_pools(self, page: int = 1, sort: str = "h24_volume_usd_desc") -> list[dict[str, Any]]:
        """Top Solana pools sorted by 24h volume (default) or other fields."""
        data = await self._get(
            "/networks/solana/pools",
            params={"page": page, "sort": sort},
        )
        return [_shape_pool(p) for p in (data.get("data") or [])]


def _shape_pool(p: dict[str, Any]) -> dict[str, Any]:
    attrs = p.get("attributes") or {}
    rels = p.get("relationships") or {}
    base_token_id = ((rels.get("base_token") or {}).get("data") or {}).get("id") or ""
    base_mint = base_token_id.split("_", 1)[1] if "_" in base_token_id else base_token_id
    created_iso = attrs.get("pool_created_at")
    created_at: datetime | None = None
    if created_iso:
        try:
            created_at = datetime.fromisoformat(created_iso.replace("Z", "+00:00"))
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
        except Exception:
            created_at = None
    price_change = attrs.get("price_change_percentage") or {}
    return {
        "pool_address": attrs.get("address") or "",
        "name": attrs.get("name") or "",
        "base_mint": base_mint,
        "base_symbol": (attrs.get("name") or "").split(" / ")[0],
        "fdv_usd": float(attrs.get("fdv_usd") or 0.0),
        "reserve_usd": float(attrs.get("reserve_in_usd") or 0.0),
        "h24_volume_usd": float((attrs.get("volume_usd") or {}).get("h24") or 0.0),
        "h24_price_change_pct": float(price_change.get("h24") or 0.0),
        "pool_created_at": created_at,
        "dex_id": ((rels.get("dex") or {}).get("data") or {}).get("id") or "",
    }
