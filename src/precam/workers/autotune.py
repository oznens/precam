"""Auto-tune KOL weights + prune smart-money watch list from backtest results."""

from dataclasses import dataclass
from typing import Literal

from loguru import logger
from sqlalchemy import select

from ..db import Kol, SessionLocal, Wallet
from .backtest import leaderboard as backtest_leaderboard


def expectancy_to_weight(exp_pct: float) -> float:
    """Map backtest expectancy (% per trade) to a KOL weight in [0.1, 3.0]."""
    if exp_pct >= 50:
        return 3.0
    if exp_pct >= 20:
        return 2.0
    if exp_pct >= 5:
        return 1.0
    if exp_pct >= 0:
        return 0.5
    return 0.1


@dataclass
class KolChange:
    handle: str
    closed: int
    expectancy_pct: float
    old_weight: float
    suggested: float
    new_weight: float
    action: Literal["raise", "lower", "noop"]


async def autotune_kol_weights(
    run_id: int, *, alpha: float = 0.5, min_closed: int = 5, dry_run: bool = True
) -> list[KolChange]:
    """Recompute KOL weights from a backtest run's leaderboard with EMA-style smoothing.

    `alpha` in [0,1] controls how aggressively we move toward the suggestion:
    0.5 = halfway, 1.0 = replace immediately, 0.0 = no change.
    """
    if not 0.0 <= alpha <= 1.0:
        raise ValueError("alpha must be in [0, 1]")

    lb = await backtest_leaderboard(run_id, min_trades=min_closed)
    if not lb:
        logger.warning(f"run {run_id}: no qualifying leaderboard rows; nothing to tune")
        return []

    changes: list[KolChange] = []
    async with SessionLocal() as s:
        for row in lb:
            handle = row["source_key"]
            kol = (
                await s.execute(select(Kol).where(Kol.handle == handle))
            ).scalar_one_or_none()
            if kol is None:
                continue
            suggested = expectancy_to_weight(row["expectancy"])
            new_w = round(kol.weight * (1 - alpha) + suggested * alpha, 2)
            action: Literal["raise", "lower", "noop"]
            if abs(new_w - kol.weight) < 0.05:
                action = "noop"
            elif new_w > kol.weight:
                action = "raise"
            else:
                action = "lower"
            changes.append(
                KolChange(
                    handle=handle,
                    closed=row["closed"],
                    expectancy_pct=row["expectancy"],
                    old_weight=kol.weight,
                    suggested=suggested,
                    new_weight=new_w,
                    action=action,
                )
            )
            if not dry_run and action != "noop":
                kol.weight = new_w
                s.add(kol)
        if not dry_run:
            await s.commit()
    return changes


@dataclass
class WatchChange:
    address: str
    label: str
    closed: int
    expectancy_pct: float
    action: Literal["unwatch", "keep"]


async def prune_watchlist(
    run_id: int,
    *,
    min_expectancy: float = 5.0,
    min_closed: int = 5,
    max_unwatch_fraction: float = 0.5,
    dry_run: bool = True,
) -> list[WatchChange]:
    """Unwatch wallets whose backtest expectancy is below `min_expectancy`.

    Safety: never unwatch more than `max_unwatch_fraction` of the current list
    in a single pass (sorted ascending by expectancy so the worst go first).
    """
    lb = await backtest_leaderboard(run_id, min_trades=min_closed)
    by_key = {r["source_key"]: r for r in lb}

    async with SessionLocal() as s:
        watched = list(
            (
                await s.execute(select(Wallet).where(Wallet.is_watched == True))  # noqa: E712
            ).scalars().all()
        )

    if not watched:
        return []

    candidates: list[WatchChange] = []
    for w in watched:
        row = by_key.get(w.address)
        if row is None:
            continue
        exp = float(row["expectancy"])
        action: Literal["unwatch", "keep"] = (
            "unwatch" if exp < min_expectancy else "keep"
        )
        candidates.append(
            WatchChange(
                address=w.address,
                label=w.label,
                closed=int(row["closed"]),
                expectancy_pct=exp,
                action=action,
            )
        )

    to_unwatch = [c for c in candidates if c.action == "unwatch"]
    cap = max(1, int(len(watched) * max_unwatch_fraction))
    if len(to_unwatch) > cap:
        to_unwatch.sort(key=lambda c: c.expectancy_pct)
        keep_them = set(c.address for c in to_unwatch[cap:])
        for c in candidates:
            if c.address in keep_them:
                c.action = "keep"
        logger.warning(
            f"unwatch capped at {cap}/{len(watched)} (would have removed {len(to_unwatch)})"
        )

    if not dry_run:
        async with SessionLocal() as s:
            for c in candidates:
                if c.action != "unwatch":
                    continue
                w = (
                    await s.execute(select(Wallet).where(Wallet.address == c.address))
                ).scalar_one_or_none()
                if w:
                    w.is_watched = False
                    s.add(w)
            await s.commit()

    return candidates
