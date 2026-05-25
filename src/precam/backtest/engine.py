"""Forward-walk a price series for one entry and decide where to exit."""

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class TPSL:
    tp_pct: float
    sl_pct: float
    max_hold_min: int

    def __post_init__(self) -> None:
        assert self.tp_pct > 0, "tp_pct must be positive (e.g. 100 for +100%)"
        assert self.sl_pct < 0, "sl_pct must be negative (e.g. -30 for -30%)"


@dataclass
class SimResult:
    exit_reason: str
    exit_price: float
    exit_ts: datetime
    pnl_pct: float
    hold_minutes: float
    max_favourable_pct: float
    max_adverse_pct: float


def simulate(
    *,
    entry_price: float,
    entry_ts: datetime,
    bars: list[list[float]],
    strategy: TPSL,
) -> SimResult | None:
    """Walk `bars` (oldest-first) starting after entry_ts. Stop on TP / SL / timeout.

    Pessimistic rule when a single bar's high >= TP AND low <= SL: stop-loss wins.
    Returns None if no usable bars are after entry_ts.
    """
    if entry_price <= 0 or not bars:
        return None

    if entry_ts.tzinfo is None:
        entry_unix = int(entry_ts.replace(tzinfo=timezone.utc).timestamp())
    else:
        entry_unix = int(entry_ts.timestamp())

    tp_price = entry_price * (1 + strategy.tp_pct / 100.0)
    sl_price = entry_price * (1 + strategy.sl_pct / 100.0)

    forward = [b for b in bars if b[0] >= entry_unix]
    if not forward:
        return None

    max_fav = 0.0
    max_adv = 0.0

    for ts, _o, h, l, c, _v in forward:
        up = (h / entry_price - 1) * 100
        dn = (l / entry_price - 1) * 100
        if up > max_fav:
            max_fav = up
        if dn < max_adv:
            max_adv = dn

        hit_sl = l <= sl_price
        hit_tp = h >= tp_price
        if hit_sl:
            return SimResult(
                exit_reason="sl",
                exit_price=sl_price,
                exit_ts=datetime.fromtimestamp(ts, tz=timezone.utc),
                pnl_pct=strategy.sl_pct,
                hold_minutes=(ts - entry_unix) / 60.0,
                max_favourable_pct=round(max_fav, 2),
                max_adverse_pct=round(max_adv, 2),
            )
        if hit_tp:
            return SimResult(
                exit_reason="tp",
                exit_price=tp_price,
                exit_ts=datetime.fromtimestamp(ts, tz=timezone.utc),
                pnl_pct=strategy.tp_pct,
                hold_minutes=(ts - entry_unix) / 60.0,
                max_favourable_pct=round(max_fav, 2),
                max_adverse_pct=round(max_adv, 2),
            )

        held = (ts - entry_unix) / 60.0
        if held >= strategy.max_hold_min:
            return SimResult(
                exit_reason="timeout",
                exit_price=c,
                exit_ts=datetime.fromtimestamp(ts, tz=timezone.utc),
                pnl_pct=round((c / entry_price - 1) * 100, 2),
                hold_minutes=held,
                max_favourable_pct=round(max_fav, 2),
                max_adverse_pct=round(max_adv, 2),
            )

    last = forward[-1]
    return SimResult(
        exit_reason="end_of_data",
        exit_price=last[4],
        exit_ts=datetime.fromtimestamp(last[0], tz=timezone.utc),
        pnl_pct=round((last[4] / entry_price - 1) * 100, 2),
        hold_minutes=(last[0] - entry_unix) / 60.0,
        max_favourable_pct=round(max_fav, 2),
        max_adverse_pct=round(max_adv, 2),
    )
