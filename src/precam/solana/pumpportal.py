"""PumpPortal websocket subscriber for new Pump.fun token creations (free, no auth).

Stream URL: wss://pumpportal.fun/api/data
Subscription: {"method": "subscribeNewToken"}

Each message looks like:
  {
    "txType": "create",
    "signature": "...",
    "mint": "...",
    "traderPublicKey": "<creator wallet>",
    "name": "...", "symbol": "...", "uri": "...",
    "initialBuy": 1000000.0,           # token amount creator bought
    "solAmount": 0.5,                  # SOL the creator put in
    "vTokensInBondingCurve": ...,
    "vSolInBondingCurve": ...,
    "marketCapSol": ...,
    "bondingCurveKey": "...",
    "pool": "pump"
  }
"""

import asyncio
import json
from datetime import datetime
from typing import AsyncIterator

import websockets
from loguru import logger

WS_URL = "wss://pumpportal.fun/api/data"
RECONNECT_DELAY = 3.0


async def stream_new_tokens() -> AsyncIterator[dict]:
    """Yield new-token events forever; reconnect on disconnect."""
    while True:
        try:
            async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=10) as ws:
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                logger.info("PumpPortal subscribed to new-token stream")
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue
                    if msg.get("txType") == "create" and msg.get("mint"):
                        yield msg
        except Exception as e:
            logger.warning(f"pump ws disconnected: {e}; reconnecting in {RECONNECT_DELAY}s")
            await asyncio.sleep(RECONNECT_DELAY)


def event_to_record(msg: dict) -> dict:
    """Normalize a PumpPortal event into a PumpToken row dict."""
    return {
        "mint": msg["mint"],
        "name": msg.get("name") or "",
        "symbol": msg.get("symbol") or "",
        "uri": msg.get("uri") or "",
        "creator": msg.get("traderPublicKey") or "",
        "created_at": datetime.utcnow(),
        "create_sig": msg.get("signature") or "",
        "initial_buy_sol": float(msg.get("solAmount") or 0.0),
        "v_sol_in_bc": float(msg.get("vSolInBondingCurve") or 0.0),
        "v_tokens_in_bc": float(msg.get("vTokensInBondingCurve") or 0.0),
        "market_cap_sol": float(msg.get("marketCapSol") or 0.0),
    }
