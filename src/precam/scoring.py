from datetime import datetime, timezone
from typing import Any

from .config import settings


def age_minutes(pair_created_at: datetime | None) -> float:
    if pair_created_at is None:
        return 9_999_999.0
    if pair_created_at.tzinfo is None:
        pair_created_at = pair_created_at.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - pair_created_at
    return max(delta.total_seconds() / 60.0, 0.0)


def score_token(meta: dict[str, Any], kol_weight: float = 1.0) -> tuple[float, bool]:
    """Return (score 0-100, is_early). Higher = more interesting early find."""
    liq = float(meta.get("liquidity_usd") or 0.0)
    vol = float(meta.get("volume_h24") or 0.0)
    buys = int(meta.get("tx_h24_buys") or 0)
    sells = int(meta.get("tx_h24_sells") or 0)
    age = age_minutes(meta.get("pair_created_at"))

    if age <= 60:
        age_score = 40.0
    elif age <= settings.early_max_age_min:
        age_score = 30.0 * (1 - (age - 60) / max(settings.early_max_age_min - 60, 1))
    else:
        age_score = 0.0

    liq_score = min(liq / 50_000.0, 1.0) * 20.0

    if vol > 0 and liq > 0:
        vol_score = min(vol / max(liq, 1.0), 5.0) / 5.0 * 20.0
    else:
        vol_score = 0.0

    total = buys + sells
    if total > 0:
        bratio = buys / total
        bias_score = max(0.0, (bratio - 0.5) * 2) * 10.0
    else:
        bias_score = 0.0

    kol_score = min(kol_weight, 3.0) / 3.0 * 10.0

    score = age_score + liq_score + vol_score + bias_score + kol_score
    is_early = age <= settings.early_max_age_min and liq >= settings.min_liquidity_usd
    return round(score, 2), is_early
