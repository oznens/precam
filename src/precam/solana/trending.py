import asyncio
from datetime import datetime, timezone
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

BASE = "https://api.geckoterminal.com/api/v2"


class GeckoTerminalClient:
    """Keyless GeckoTerminal API. Used to seed wallet discovery with trending pools."""

    def __init__(self, timeout: float = 12.0) -> None:
        self.timeout = timeout

    @retry(stop=stop_after_attempt(4), wait=wait_exponential(min=2, max=20))
    async def _get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.get(f"{BASE}{path}", params=params, headers={"accept": "application/json"})
            r.raise_for_status()
            return r.json()

    async def trending_pools(
        self, page: int = 1, duration: str = "24h", pages: int = 1
    ) -> list[dict[str, Any]]:
        """Return Solana trending pools. duration in {5m, 1h, 6h, 24h}.

        GeckoTerminal returns 20 pools per page, up to page 10. Set `pages` > 1
        to walk multiple pages and merge (deduped on pool address)."""
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for i, p in enumerate(range(page, page + pages)):
            if i > 0:
                await asyncio.sleep(2.0)
            data = await self._get(
                "/networks/solana/trending_pools",
                params={"page": p, "duration": duration},
            )
            batch = [_shape_pool(x) for x in (data.get("data") or [])]
            if not batch:
                break
            for pool in batch:
                if pool["pool_address"] in seen:
                    continue
                seen.add(pool["pool_address"])
                out.append(pool)
        return out

    async def top_pools(self, page: int = 1, sort: str = "h24_volume_usd_desc") -> list[dict[str, Any]]:
        """Top Solana pools sorted by 24h volume (default) or other fields."""
        data = await self._get(
            "/networks/solana/pools",
            params={"page": page, "sort": sort},
        )
        return [_shape_pool(p) for p in (data.get("data") or [])]

    async def top_pool_for_token(self, mint: str) -> str | None:
        """Return the highest-h24-volume pool address for a mint, or None."""
        data = await self._get(f"/networks/solana/tokens/{mint}/pools")
        pools = data.get("data") or []
        best_addr: str | None = None
        best_vol = -1.0
        for p in pools:
            attrs = p.get("attributes") or {}
            vol = float((attrs.get("volume_usd") or {}).get("h24") or 0.0)
            if vol > best_vol:
                best_vol = vol
                best_addr = attrs.get("address")
        return best_addr

    async def pool_trades(self, pool_address: str) -> list[dict[str, Any]]:
        """Return up to ~300 most recent trades on a pool with wallet addresses.

        Each trade has kind (buy/sell), volume_in_usd, tx_from_address (the
        trader's wallet), and block_timestamp. This is the discovery primitive
        for co-buyer detection: trades on a hot pool reveal which other wallets
        are stepping into the same name our watched wallets just bought.
        """
        data = await self._get(f"/networks/solana/pools/{pool_address}/trades")
        out: list[dict[str, Any]] = []
        for t in data.get("data") or []:
            a = t.get("attributes") or {}
            ts_iso = a.get("block_timestamp")
            ts: datetime | None = None
            if ts_iso:
                try:
                    ts = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                except Exception:
                    ts = None
            out.append(
                {
                    "kind": a.get("kind") or "",
                    "wallet": a.get("tx_from_address") or "",
                    "volume_usd": float(a.get("volume_in_usd") or 0.0),
                    "block_time": ts,
                }
            )
        return out

    async def new_pools(self, pages: int = 1) -> list[dict[str, Any]]:
        """Recently created Solana pools (~newest first). 20 per page, max page 10.

        Sleeps between pages because GeckoTerminal page>1 isn't CF-cached and
        will 429 if hit faster than ~1 req/sec.
        """
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for i, p in enumerate(range(1, pages + 1)):
            if i > 0:
                await asyncio.sleep(2.0)
            data = await self._get(
                "/networks/solana/new_pools",
                params={"page": p},
            )
            batch = [_shape_pool(x) for x in (data.get("data") or [])]
            if not batch:
                break
            for pool in batch:
                if pool["pool_address"] in seen:
                    continue
                seen.add(pool["pool_address"])
                out.append(pool)
        return out


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
