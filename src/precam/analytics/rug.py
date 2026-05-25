"""Rug-risk heuristics for a Pump.fun token.

Score is 0-100 — higher = riskier. A token is `clean` if rug_risk < threshold
AND creator share is bounded AND mint authority is revoked.

Heuristics (each adds to risk):
- creator share > 5%      : +30   (dev holds too much; can dump)
- creator share > 15%     : +20   (red flag bonus on top of above)
- top10 share  > 50%      : +20   (concentrated; few wallets control supply)
- top10 share  > 80%      : +20   (extreme; almost certainly bundled)
- mint authority active   : +15   (creator can mint infinite tokens)
- freeze authority active : +10   (creator can freeze your tokens)
- holders < 20            : +10   (too fresh to gauge real distribution)
- initial_buy > 5 SOL     : +10   (creator pre-mined a huge bag)
- initial_buy > 15 SOL    : +10   (sniper-launch pattern)
"""

from typing import Any


def score_rug(
    *,
    creator_share: float,
    top10_share: float,
    holders_count: int,
    mint_auth_revoked: bool,
    freeze_auth_revoked: bool,
    initial_buy_sol: float,
) -> float:
    risk = 0.0
    if creator_share > 0.05:
        risk += 30
    if creator_share > 0.15:
        risk += 20
    if top10_share > 0.50:
        risk += 20
    if top10_share > 0.80:
        risk += 20
    if not mint_auth_revoked:
        risk += 15
    if not freeze_auth_revoked:
        risk += 10
    if holders_count < 20:
        risk += 10
    if initial_buy_sol > 5:
        risk += 10
    if initial_buy_sol > 15:
        risk += 10
    return min(risk, 100.0)


def is_clean(
    *,
    rug_risk: float,
    creator_share: float,
    mint_auth_revoked: bool,
    holders_count: int,
    risk_threshold: float = 30.0,
    max_creator_share: float = 0.05,
    min_holders: int = 25,
) -> bool:
    return (
        rug_risk < risk_threshold
        and creator_share <= max_creator_share
        and mint_auth_revoked
        and holders_count >= min_holders
    )


def supply_from_largest_accounts(largest: list[dict[str, Any]]) -> tuple[float, float]:
    """Helius/RPC getTokenLargestAccounts -> (total_in_top, weighted_total).

    Each entry has uiAmount (human-readable). For top-N share we need the actual
    token supply for the denominator — pass that in separately. This helper just
    returns sum_top10_ui."""
    top10 = largest[:10]
    top10_sum = 0.0
    for h in top10:
        try:
            top10_sum += float(h.get("uiAmount") or 0.0)
        except Exception:
            pass
    return top10_sum, sum(float(h.get("uiAmount") or 0.0) for h in largest)
