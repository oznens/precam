"""Parse Helius enhanced SWAP transactions into normalized (wallet, mint, side, amount) trades.

Helius events.swap shape (relevant subset):
  {
    "nativeInput":  {"account": "...", "amount": "<lamports str>"} | null,
    "nativeOutput": {...} | null,
    "tokenInputs":  [{"userAccount": "...", "mint": "...", "rawTokenAmount": {...}} | with "tokenAmount" float],
    "tokenOutputs": [...],
    "innerSwaps":   [...]
  }
"""

from datetime import datetime, timezone
from typing import Any

SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
QUOTE_MINTS = {SOL_MINT, USDC_MINT, USDT_MINT}
LAMPORTS_PER_SOL = 1_000_000_000


def _token_amount(item: dict[str, Any]) -> float:
    """Extract human-readable amount from a Helius token input/output item."""
    if "tokenAmount" in item:
        try:
            return float(item["tokenAmount"])
        except Exception:
            return 0.0
    raw = item.get("rawTokenAmount") or {}
    amt = raw.get("tokenAmount")
    decimals = raw.get("decimals")
    if amt is None or decimals is None:
        return 0.0
    try:
        return float(amt) / (10 ** int(decimals))
    except Exception:
        return 0.0


def _normalize_side(items: list[dict[str, Any]], native: dict[str, Any] | None) -> list[tuple[str, float]]:
    """Return list of (mint, human_amount) for one side of a swap (inputs or outputs)."""
    out: list[tuple[str, float]] = []
    for it in items or []:
        mint = it.get("mint") or ""
        if not mint:
            continue
        out.append((mint, _token_amount(it)))
    if native and native.get("amount") is not None:
        try:
            out.append((SOL_MINT, float(native["amount"]) / LAMPORTS_PER_SOL))
        except Exception:
            pass
    return out


def _infer_from_token_transfers(tx: dict[str, Any]) -> dict[str, Any]:
    """Fallback when events.swap is empty (Helius hasn't parsed the DEX yet, e.g. PUMP_AMM).

    Reconstruct an events.swap-shaped dict from top-level tokenTransfers and
    nativeTransfers, restricted to the feePayer's perspective:
      tokenInputs  = tokens the feePayer SENT
      tokenOutputs = tokens the feePayer RECEIVED
      nativeInput  = SOL the feePayer SENT (aggregated)
      nativeOutput = SOL the feePayer RECEIVED (aggregated)
    """
    trader = tx.get("feePayer") or ""
    if not trader:
        return {}

    tt = tx.get("tokenTransfers") or []
    nt = tx.get("nativeTransfers") or []

    sent_tokens: dict[str, float] = {}
    recv_tokens: dict[str, float] = {}
    sent_lamports = 0
    recv_lamports = 0

    for tr in tt:
        mint = tr.get("mint")
        if not mint:
            continue
        try:
            amt = float(tr.get("tokenAmount") or 0.0)
        except Exception:
            amt = 0.0
        if amt <= 0:
            continue
        if tr.get("fromUserAccount") == trader:
            sent_tokens[mint] = sent_tokens.get(mint, 0.0) + amt
        if tr.get("toUserAccount") == trader:
            recv_tokens[mint] = recv_tokens.get(mint, 0.0) + amt

    for nx in nt:
        try:
            amt = int(nx.get("amount") or 0)
        except Exception:
            amt = 0
        if amt <= 0:
            continue
        if nx.get("fromUserAccount") == trader:
            sent_lamports += amt
        if nx.get("toUserAccount") == trader:
            recv_lamports += amt

    if not sent_tokens and not recv_tokens and not sent_lamports and not recv_lamports:
        return {}

    return {
        "tokenInputs":  [{"mint": m, "tokenAmount": a} for m, a in sent_tokens.items()],
        "tokenOutputs": [{"mint": m, "tokenAmount": a} for m, a in recv_tokens.items()],
        "nativeInput":  ({"account": trader, "amount": str(sent_lamports)} if sent_lamports else None),
        "nativeOutput": ({"account": trader, "amount": str(recv_lamports)} if recv_lamports else None),
    }


def parse_swap(tx: dict[str, Any], sol_usd: float | None = None) -> list[dict[str, Any]]:
    """Extract zero or more trade dicts from one Helius enhanced SWAP transaction.

    Falls back to inferring inputs/outputs from raw token/native transfers when
    `events.swap` is empty (Helius hasn't parsed the DEX, e.g. PUMP_AMM / pumpswap).
    """
    swap = ((tx.get("events") or {}).get("swap")) or {}
    if not swap:
        swap = _infer_from_token_transfers(tx)
    if not swap:
        return []

    trader = tx.get("feePayer") or ""
    ts = tx.get("timestamp") or 0
    block_time = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else datetime.now(timezone.utc)
    sig = tx.get("signature") or ""
    source = tx.get("source") or ""

    inputs = _normalize_side(swap.get("tokenInputs"), swap.get("nativeInput"))
    outputs = _normalize_side(swap.get("tokenOutputs"), swap.get("nativeOutput"))

    def quote_usd(side: list[tuple[str, float]]) -> tuple[float, list[tuple[str, float]]]:
        usd = 0.0
        non_quote: list[tuple[str, float]] = []
        for mint, amt in side:
            if mint == SOL_MINT:
                if sol_usd:
                    usd += amt * sol_usd
            elif mint in (USDC_MINT, USDT_MINT):
                usd += amt
            else:
                non_quote.append((mint, amt))
        return usd, non_quote

    spent_usd, tokens_spent = quote_usd(inputs)
    received_usd, tokens_received = quote_usd(outputs)

    trades: list[dict[str, Any]] = []

    if spent_usd > 0 and tokens_received:
        total = sum(a for _, a in tokens_received) or 1e-9
        per = spent_usd / total
        for mint, amt in tokens_received:
            trades.append(
                {
                    "wallet": trader, "mint": mint, "side": "buy",
                    "amount_token": amt, "amount_usd": amt * per, "price_usd": per,
                    "tx_sig": sig, "block_time": block_time, "source": source,
                }
            )

    if received_usd > 0 and tokens_spent:
        total = sum(a for _, a in tokens_spent) or 1e-9
        per = received_usd / total
        for mint, amt in tokens_spent:
            trades.append(
                {
                    "wallet": trader, "mint": mint, "side": "sell",
                    "amount_token": amt, "amount_usd": amt * per, "price_usd": per,
                    "tx_sig": sig, "block_time": block_time, "source": source,
                }
            )

    return trades
