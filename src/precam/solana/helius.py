from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import settings


class HeliusClient:
    """Minimal Helius/Solana RPC wrapper. Falls back to public RPC if no key set."""

    def __init__(self, timeout: float = 12.0) -> None:
        self.rpc = settings.helius_rpc
        self.timeout = timeout

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    async def _rpc(self, method: str, params: list[Any]) -> Any:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(self.rpc, json=payload)
            r.raise_for_status()
        body = r.json()
        if "error" in body:
            raise RuntimeError(f"RPC error: {body['error']}")
        return body.get("result")

    async def get_token_supply(self, mint: str) -> dict[str, Any] | None:
        try:
            return await self._rpc("getTokenSupply", [mint])
        except Exception:
            return None

    async def get_largest_holders(self, mint: str) -> list[dict[str, Any]]:
        try:
            res = await self._rpc("getTokenLargestAccounts", [mint])
            return (res or {}).get("value") or []
        except Exception:
            return []

    async def get_mint_info(self, mint: str) -> dict[str, Any] | None:
        """Parsed SPL mint account: supply, decimals, mintAuthority, freezeAuthority."""
        try:
            res = await self._rpc(
                "getAccountInfo", [mint, {"encoding": "jsonParsed"}]
            )
        except Exception:
            return None
        if not res or not res.get("value"):
            return None
        parsed = (((res["value"].get("data") or {}).get("parsed")) or {}).get("info") or {}
        return parsed or None

    async def get_token_accounts_by_mint(self, mint: str, limit: int = 1000) -> int:
        """Approximate holders count via getProgramAccounts on token program."""
        try:
            res = await self._rpc(
                "getProgramAccounts",
                [
                    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                    {
                        "encoding": "jsonParsed",
                        "filters": [
                            {"dataSize": 165},
                            {"memcmp": {"offset": 0, "bytes": mint}},
                        ],
                    },
                ],
            )
        except Exception:
            return 0
        return len(res or [])

    async def get_asset(self, mint: str) -> dict[str, Any] | None:
        """DAS getAsset — works only with Helius key (preferred metadata source)."""
        if not settings.helius_api_key:
            return None
        try:
            return await self._rpc("getAsset", [mint])
        except Exception:
            return None

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    async def enhanced_transactions(
        self,
        address: str,
        *,
        type_: str | None = "SWAP",
        limit: int = 100,
        before: str | None = None,
    ) -> list[dict[str, Any]]:
        """Helius parsed/enhanced transactions for an address. Requires Helius API key."""
        if not settings.helius_api_key:
            raise RuntimeError("HELIUS_API_KEY required for enhanced transactions")
        url = f"https://api.helius.xyz/v0/addresses/{address}/transactions"
        params: dict[str, Any] = {"api-key": settings.helius_api_key, "limit": limit}
        if type_:
            params["type"] = type_
        if before:
            params["before"] = before
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
        return r.json() or []

    async def iter_swaps(
        self,
        address: str,
        *,
        max_pages: int = 5,
        until_ts: int | None = None,
    ) -> list[dict[str, Any]]:
        """Walk enhanced SWAP transactions backwards in time.

        Stops after max_pages pages OR when block_time goes below until_ts.
        Returns a flat list of raw Helius enhanced tx dicts (newest first).
        """
        out: list[dict[str, Any]] = []
        before: str | None = None
        for _ in range(max_pages):
            page = await self.enhanced_transactions(address, type_="SWAP", limit=100, before=before)
            if not page:
                break
            out.extend(page)
            last = page[-1]
            before = last.get("signature")
            if until_ts is not None and (last.get("timestamp") or 0) <= until_ts:
                break
        return out
