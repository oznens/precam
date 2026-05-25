"""Build positions from a wallet's trade history and compute aggregate stats.

A "position" = the lifecycle of one (wallet, mint) pair: opens on first buy,
closes when sold_token >= bought_token (treated as fully exited).
While open, realized PnL uses the realized portion only.
"""

from datetime import datetime
from typing import Iterable

from .swaps import QUOTE_MINTS


def build_positions(trades: Iterable[dict]) -> list[dict]:
    """Reconstruct positions per (wallet, mint) from a chronological list of trades."""
    sorted_trades = sorted(trades, key=lambda t: t["block_time"])
    by_key: dict[tuple[str, str], dict] = {}

    for t in sorted_trades:
        if t["mint"] in QUOTE_MINTS:
            continue
        key = (t["wallet"], t["mint"])
        pos = by_key.get(key)
        if pos is None:
            if t["side"] != "buy":
                continue
            pos = {
                "wallet": t["wallet"],
                "mint": t["mint"],
                "status": "open",
                "opened_at": t["block_time"],
                "closed_at": None,
                "bought_token": 0.0,
                "sold_token": 0.0,
                "bought_usd": 0.0,
                "sold_usd": 0.0,
            }
            by_key[key] = pos

        if t["side"] == "buy":
            pos["bought_token"] += t["amount_token"]
            pos["bought_usd"] += t["amount_usd"]
        else:
            pos["sold_token"] += t["amount_token"]
            pos["sold_usd"] += t["amount_usd"]
            if pos["status"] == "open" and pos["sold_token"] >= pos["bought_token"] * 0.99:
                pos["status"] = "closed"
                pos["closed_at"] = t["block_time"]

    out = []
    for pos in by_key.values():
        avg_buy = pos["bought_usd"] / pos["bought_token"] if pos["bought_token"] > 0 else 0.0
        avg_sell = pos["sold_usd"] / pos["sold_token"] if pos["sold_token"] > 0 else 0.0
        realized_tokens = min(pos["sold_token"], pos["bought_token"])
        cost_basis = realized_tokens * avg_buy
        realized_usd = pos["sold_usd"] - cost_basis if avg_buy > 0 else 0.0
        realized_pct = (realized_usd / cost_basis * 100) if cost_basis > 0 else 0.0
        out.append(
            {
                **pos,
                "avg_buy_price": avg_buy,
                "avg_sell_price": avg_sell,
                "realized_pnl_usd": realized_usd,
                "realized_pnl_pct": realized_pct,
            }
        )
    return out


def compute_stats(positions: list[dict]) -> dict:
    """Aggregate closed positions into wallet-level stats (win rate, expectancy, ...)."""
    closed = [p for p in positions if p["status"] == "closed" and p["bought_usd"] > 0]
    total_positions = len(positions)
    closed_n = len(closed)
    if closed_n == 0:
        return {
            "total_positions": total_positions,
            "closed_positions": 0,
            "wins": 0, "losses": 0,
            "win_rate": 0.0,
            "avg_win_pct": 0.0,
            "avg_loss_pct": 0.0,
            "expectancy": 0.0,
            "total_realized_pnl_usd": 0.0,
            "updated_at": datetime.utcnow(),
        }

    wins = [p for p in closed if p["realized_pnl_usd"] > 0]
    losses = [p for p in closed if p["realized_pnl_usd"] <= 0]
    win_rate = len(wins) / closed_n
    avg_win = sum(p["realized_pnl_pct"] for p in wins) / len(wins) if wins else 0.0
    avg_loss = sum(p["realized_pnl_pct"] for p in losses) / len(losses) if losses else 0.0
    expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss
    total_pnl = sum(p["realized_pnl_usd"] for p in closed)

    return {
        "total_positions": total_positions,
        "closed_positions": closed_n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 4),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "expectancy": round(expectancy, 2),
        "total_realized_pnl_usd": round(total_pnl, 2),
        "updated_at": datetime.utcnow(),
    }
