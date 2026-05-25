"""Helius webhook management client.

Helius webhooks push parsed transactions to a URL when any of the
registered `accountAddresses` is involved. We use this to replace the
polling watcher: instead of asking Helius "what new swaps did this wallet
do?" every 90s, Helius itself POSTs each new swap to our endpoint.

API: https://docs.helius.dev/webhooks-and-websockets/api-reference
  POST   /v0/webhooks?api-key=KEY     create webhook
  GET    /v0/webhooks?api-key=KEY     list
  GET    /v0/webhooks/{id}?api-key=KEY
  PUT    /v0/webhooks/{id}?api-key=KEY  update
  DELETE /v0/webhooks/{id}?api-key=KEY  delete

Each webhook can subscribe up to ~100k addresses on paid plans; on free
tier the practical limit is much lower (~100). We chunk on sync.
"""

from typing import Any

import httpx
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import settings

BASE = "https://api.helius.xyz/v0/webhooks"
PRECAM_LABEL = "precam-watcher"
MAX_ADDRESSES_PER_HOOK = 100


def _require_key() -> str:
    if not settings.helius_api_key:
        raise RuntimeError("HELIUS_API_KEY required for webhook management")
    return settings.helius_api_key


def _params() -> dict[str, str]:
    return {"api-key": _require_key()}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
async def list_webhooks() -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=15.0) as c:
        r = await c.get(BASE, params=_params())
        r.raise_for_status()
    return r.json() or []


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
async def create_webhook(
    *, url: str, addresses: list[str], auth_header: str = ""
) -> dict[str, Any]:
    if not url:
        raise ValueError("webhook url required")
    body = {
        "webhookURL": url,
        "transactionTypes": ["SWAP"],
        "accountAddresses": addresses,
        "webhookType": "enhanced",
    }
    if auth_header:
        body["authHeader"] = auth_header
    async with httpx.AsyncClient(timeout=15.0) as c:
        r = await c.post(BASE, params=_params(), json=body)
        r.raise_for_status()
    return r.json()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
async def update_webhook(
    webhook_id: str, *, addresses: list[str], url: str | None = None, auth_header: str = ""
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "transactionTypes": ["SWAP"],
        "accountAddresses": addresses,
        "webhookType": "enhanced",
    }
    if url:
        body["webhookURL"] = url
    if auth_header:
        body["authHeader"] = auth_header
    async with httpx.AsyncClient(timeout=15.0) as c:
        r = await c.put(f"{BASE}/{webhook_id}", params=_params(), json=body)
        r.raise_for_status()
    return r.json()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
async def delete_webhook(webhook_id: str) -> None:
    async with httpx.AsyncClient(timeout=15.0) as c:
        r = await c.delete(f"{BASE}/{webhook_id}", params=_params())
        if r.status_code == 404:
            return
        r.raise_for_status()


async def find_precam_hooks(target_url: str) -> list[dict[str, Any]]:
    hooks = await list_webhooks()
    return [h for h in hooks if (h.get("webhookURL") or "").startswith(target_url)]


def _chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


async def sync_watched_addresses(
    addresses: list[str], *, base_url: str, auth_header: str = ""
) -> dict[str, Any]:
    """Reconcile registered Helius webhooks with the current set of watched
    wallet addresses. Each hook handles up to MAX_ADDRESSES_PER_HOOK addresses.

    Returns a summary {created, updated, deleted, total_addresses}.
    """
    url = base_url.rstrip("/") + "/webhooks/helius"
    addresses = sorted(set(a for a in addresses if a))
    desired_chunks = _chunked(addresses, MAX_ADDRESSES_PER_HOOK) if addresses else []

    existing = await find_precam_hooks(url)
    summary = {
        "created": 0,
        "updated": 0,
        "deleted": 0,
        "total_addresses": len(addresses),
        "url": url,
    }

    for i, chunk in enumerate(desired_chunks):
        if i < len(existing):
            hook_id = existing[i].get("webhookID") or existing[i].get("id") or ""
            current_addrs = set(existing[i].get("accountAddresses") or [])
            if current_addrs == set(chunk):
                continue
            await update_webhook(hook_id, addresses=chunk, url=url, auth_header=auth_header)
            summary["updated"] += 1
            logger.success(f"updated webhook {hook_id} ({len(chunk)} addrs)")
        else:
            res = await create_webhook(url=url, addresses=chunk, auth_header=auth_header)
            summary["created"] += 1
            logger.success(
                f"created webhook {res.get('webhookID') or '?'} ({len(chunk)} addrs)"
            )

    for h in existing[len(desired_chunks):]:
        hid = h.get("webhookID") or h.get("id") or ""
        if not hid:
            continue
        await delete_webhook(hid)
        summary["deleted"] += 1
        logger.success(f"deleted stale webhook {hid}")

    return summary
