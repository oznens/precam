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

    async def get_asset(self, mint: str) -> dict[str, Any] | None:
        """DAS getAsset — works only with Helius key (preferred metadata source)."""
        if not settings.helius_api_key:
            return None
        try:
            return await self._rpc("getAsset", [mint])
        except Exception:
            return None
