"""Pump.fun new-token listener + rug rescorer + alerter."""

import asyncio
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select

from ..analytics.rug import is_clean, score_rug
from ..config import settings
from ..db import PumpToken, SessionLocal
from ..notifier import telegram
from ..solana.helius import HeliusClient
from ..solana.pumpportal import event_to_record, stream_new_tokens

PUMP_PROGRAM_PDAS = {
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
}


async def listen() -> None:
    """Run the websocket listener forever, persisting each new token."""
    saved = 0
    async for evt in stream_new_tokens():
        rec = event_to_record(evt)
        try:
            async with SessionLocal() as s:
                res = await s.execute(select(PumpToken).where(PumpToken.mint == rec["mint"]))
                if res.scalar_one_or_none():
                    continue
                s.add(PumpToken(**rec))
                await s.commit()
            saved += 1
            if saved % 50 == 0:
                logger.info(f"pump.listen saved {saved} new tokens so far")
        except Exception as e:
            logger.error(f"pump.listen save failed: {e}")


async def _score_one(token: PumpToken, helius: HeliusClient) -> None:
    """Pull on-chain data and compute rug metrics for a single token."""
    mint_info = await helius.get_mint_info(token.mint)
    if not mint_info:
        logger.debug(f"{token.symbol or token.mint[:6]}: no mint info yet")
        return

    decimals = int(mint_info.get("decimals") or 0)
    raw_supply = int(mint_info.get("supply") or 0)
    supply_ui = raw_supply / (10 ** decimals) if decimals > 0 else float(raw_supply)
    if supply_ui <= 0:
        return

    mint_authority = mint_info.get("mintAuthority")
    freeze_authority = mint_info.get("freezeAuthority")
    token.mint_auth_revoked = mint_authority in (None, "")
    token.freeze_auth_revoked = freeze_authority in (None, "")

    largest = await helius.get_largest_holders(token.mint)
    if not largest:
        return

    def _ui(a: dict) -> float:
        try:
            return float(a.get("uiAmount") or 0.0)
        except Exception:
            return 0.0

    bc_balance = 0.0
    for h in largest:
        addr = h.get("address") or ""
        if addr in PUMP_PROGRAM_PDAS or addr == token.create_sig:
            bc_balance += _ui(h)

    human_holders = [
        h for h in largest if (h.get("address") or "") not in PUMP_PROGRAM_PDAS
    ]
    human_holders.sort(key=_ui, reverse=True)

    creator_balance = 0.0
    if token.creator:
        for h in human_holders:
            if h.get("owner") == token.creator or h.get("address") == token.creator:
                creator_balance += _ui(h)

    circulating = max(supply_ui - bc_balance, 1.0)
    top10_sum = sum(_ui(h) for h in human_holders[:10])
    token.creator_share = min(creator_balance / circulating, 1.0)
    token.top10_share = min(top10_sum / circulating, 1.0)
    token.holders_count = len([h for h in human_holders if _ui(h) > 0])

    token.rug_risk = score_rug(
        creator_share=token.creator_share,
        top10_share=token.top10_share,
        holders_count=token.holders_count,
        mint_auth_revoked=token.mint_auth_revoked,
        freeze_auth_revoked=token.freeze_auth_revoked,
        initial_buy_sol=token.initial_buy_sol,
    )
    token.is_clean = is_clean(
        rug_risk=token.rug_risk,
        creator_share=token.creator_share,
        mint_auth_revoked=token.mint_auth_revoked,
        holders_count=token.holders_count,
    )
    token.last_checked_at = datetime.utcnow()


async def rescore_pending(*, batch: int = 25, concurrency: int = 4) -> int:
    """Score the oldest unscored / stalest tokens (oldest last_checked_at first)."""
    if not settings.helius_api_key:
        logger.error("HELIUS_API_KEY required for pump rescore")
        return 0

    helius = HeliusClient()
    async with SessionLocal() as s:
        res = await s.execute(
            select(PumpToken)
            .order_by(PumpToken.last_checked_at.asc().nulls_first())
            .limit(batch)
        )
        tokens = list(res.scalars().all())
    if not tokens:
        return 0

    sem = asyncio.Semaphore(concurrency)

    async def _bound(tok: PumpToken):
        async with sem:
            try:
                await _score_one(tok, helius)
            except Exception as e:
                logger.warning(f"score {tok.mint[:6]}: {e}")

    await asyncio.gather(*(_bound(t) for t in tokens))

    alerted = 0
    async with SessionLocal() as s:
        for t in tokens:
            s.add(t)
            if t.is_clean and not t.alerted:
                msg = _format_alert(t)
                if await telegram.send(msg):
                    t.alerted = True
                    s.add(t)
                    alerted += 1
        await s.commit()

    logger.success(f"pump.rescore: {len(tokens)} scored, {alerted} new alert(s)")
    return alerted


def _format_alert(t: PumpToken) -> str:
    import html as _html

    name = _html.escape(t.name or "?")
    symbol = _html.escape((t.symbol or "?").lstrip("$"))
    return (
        f"🧼 <b>CLEAN PUMP</b> — <b>{name}</b> (${symbol})\n"
        f"rug_risk <b>{t.rug_risk:.0f}/100</b>  "
        f"creator <b>{t.creator_share*100:.1f}%</b>  "
        f"top10 <b>{t.top10_share*100:.1f}%</b>  "
        f"holders <b>{t.holders_count}</b>\n"
        f"mint_auth {'✅revoked' if t.mint_auth_revoked else '❌active'}  "
        f"freeze {'✅revoked' if t.freeze_auth_revoked else '❌active'}\n"
        f"initial buy: <b>{t.initial_buy_sol:.2f} SOL</b>  "
        f"mcap ≈ <b>{t.market_cap_sol:.1f} SOL</b>\n\n"
        f"<code>{t.mint}</code>\n"
        f'<a href="https://pump.fun/coin/{t.mint}">pump.fun</a> · '
        f'<a href="https://gmgn.ai/sol/token/{t.mint}">GMGN</a> · '
        f'<a href="https://solscan.io/token/{t.mint}">solscan</a>'
    )


async def rescore_loop(*, interval: int = 60) -> None:
    """Keep rescoring oldest tokens forever, throttled by `interval` seconds."""
    while True:
        try:
            await rescore_pending()
        except Exception as e:
            logger.exception(f"rescore loop error: {e}")
        await asyncio.sleep(interval)
