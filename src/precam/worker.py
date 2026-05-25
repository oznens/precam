import asyncio
from datetime import datetime

from loguru import logger
from sqlalchemy import select

from .config import settings
from .db import Kol, Signal, SessionLocal, Tweet, init_db
from .notifier import telegram
from .scoring import age_minutes, score_token
from .solana.dexscreener import DexScreenerClient
from .solana.extractor import extract_candidates
from .twitter.scraper import latest_tweets


async def _seen_tweet(session, tweet_id: str) -> bool:
    res = await session.execute(select(Tweet).where(Tweet.tweet_id == tweet_id))
    return res.scalar_one_or_none() is not None


async def _seen_signal(session, handle: str, mint: str) -> bool:
    res = await session.execute(
        select(Signal).where(Signal.handle == handle, Signal.mint == mint)
    )
    return res.scalar_one_or_none() is not None


async def _process_kol(kol: Kol, dex: DexScreenerClient) -> int:
    new_signals = 0
    async with SessionLocal() as session:
        async for tw in latest_tweets(kol.handle, limit=20):
            if await _seen_tweet(session, tw.tweet_id):
                continue

            session.add(
                Tweet(
                    tweet_id=tw.tweet_id,
                    handle=tw.handle,
                    posted_at=tw.posted_at,
                    text=tw.text,
                    url=tw.url,
                )
            )

            mints = extract_candidates(tw.text)
            for mint in mints:
                if await _seen_signal(session, kol.handle, mint):
                    continue
                meta = await dex.get_token(mint)
                if not meta:
                    logger.info(f"@{kol.handle} -> {mint}: no DexScreener match (skip)")
                    continue

                score, is_early = score_token(meta, kol_weight=kol.weight)
                sig = Signal(
                    mint=mint,
                    handle=kol.handle,
                    tweet_id=tw.tweet_id,
                    tweet_url=tw.url,
                    name=meta["name"],
                    symbol=meta["symbol"],
                    price_usd=meta["price_usd"],
                    liquidity_usd=meta["liquidity_usd"],
                    fdv_usd=meta["fdv_usd"],
                    pair_created_at=meta["pair_created_at"],
                    age_min=age_minutes(meta["pair_created_at"]),
                    score=score,
                    is_early=is_early,
                    dex_url=meta["dex_url"],
                )
                session.add(sig)
                new_signals += 1

                if sig.liquidity_usd >= settings.min_liquidity_usd:
                    msg = telegram.build_message(sig.model_dump(), meta)
                    await telegram.send(msg)
                else:
                    logger.info(
                        f"@{kol.handle} -> {meta['symbol']} liq {meta['liquidity_usd']:.0f} "
                        f"below threshold; stored only"
                    )

        kol.last_checked_at = datetime.utcnow()
        session.add(kol)
        await session.commit()
    return new_signals


async def scan_once() -> int:
    dex = DexScreenerClient()
    total = 0
    async with SessionLocal() as session:
        res = await session.execute(select(Kol))
        kols = list(res.scalars().all())
    if not kols:
        logger.warning("No KOLs configured. Run `precam kol add <handle>` first.")
        return 0
    for kol in kols:
        try:
            n = await _process_kol(kol, dex)
            total += n
            if n:
                logger.success(f"@{kol.handle}: {n} new signal(s)")
        except Exception as e:
            logger.exception(f"@{kol.handle}: scan failed: {e}")
    return total


async def run_forever() -> None:
    await init_db()
    logger.info(f"Worker started. Scan interval={settings.scan_interval}s")
    while True:
        try:
            await scan_once()
        except Exception as e:
            logger.exception(f"scan loop error: {e}")
        await asyncio.sleep(settings.scan_interval)
