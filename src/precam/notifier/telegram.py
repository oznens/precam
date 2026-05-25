import html

import httpx
from loguru import logger

from ..config import settings


def _fmt_usd(v: float) -> str:
    if v >= 1_000_000:
        return f"${v/1_000_000:.2f}M"
    if v >= 1_000:
        return f"${v/1_000:.1f}K"
    if v >= 1:
        return f"${v:.2f}"
    if v > 0:
        return f"${v:.6f}".rstrip("0").rstrip(".")
    return "$0"


def _fmt_age(min_age: float) -> str:
    if min_age < 60:
        return f"{min_age:.0f}m"
    if min_age < 24 * 60:
        return f"{min_age/60:.1f}h"
    return f"{min_age/1440:.1f}d"


def build_message(signal: dict, meta: dict) -> str:
    name = html.escape(signal["name"] or "?")
    symbol = html.escape((signal["symbol"] or "?").lstrip("$"))
    handle = html.escape(signal["handle"])
    mint = signal["mint"]
    tag = "🚨 <b>EARLY</b>" if signal.get("is_early") else "🔔 SIGNAL"
    return (
        f"{tag} — <b>{name}</b> (${symbol})\n"
        f"by @{handle}  |  score <b>{signal['score']:.0f}</b>\n"
        f"price {_fmt_usd(meta.get('price_usd', 0))}  "
        f"liq {_fmt_usd(meta.get('liquidity_usd', 0))}  "
        f"fdv {_fmt_usd(meta.get('fdv_usd', 0))}\n"
        f"age {_fmt_age(signal['age_min'])}  "
        f"buys/sells 24h: {meta.get('tx_h24_buys', 0)}/{meta.get('tx_h24_sells', 0)}\n\n"
        f"<code>{mint}</code>\n"
        f'<a href="{signal["dex_url"]}">DexScreener</a> · '
        f'<a href="https://gmgn.ai/sol/token/{mint}">GMGN</a> · '
        f'<a href="https://birdeye.so/token/{mint}?chain=solana">Birdeye</a> · '
        f'<a href="{signal["tweet_url"]}">tweet</a>'
    )


async def send(text: str) -> bool:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        logger.warning("Telegram not configured; skipping send")
        return False
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": settings.telegram_chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(url, json=payload)
            r.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
        return False
