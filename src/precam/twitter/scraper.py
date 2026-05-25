from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

from loguru import logger
from twscrape import API

from ..config import settings


@dataclass
class TweetItem:
    tweet_id: str
    handle: str
    text: str
    url: str
    posted_at: datetime


_API: API | None = None


def _api_db_path() -> str:
    return str(settings.project_root / "data" / "runtime" / "twscrape.db")


async def get_api() -> API:
    global _API
    if _API is None:
        Path(_api_db_path()).parent.mkdir(parents=True, exist_ok=True)
        _API = API(_api_db_path())
    return _API


async def latest_tweets(handle: str, limit: int = 20) -> AsyncIterator[TweetItem]:
    """Yield recent tweets (incl. retweets) for a handle. Newest first."""
    api = await get_api()
    handle_clean = handle.lstrip("@")
    try:
        user = await api.user_by_login(handle_clean)
    except Exception as e:
        logger.warning(f"@{handle_clean}: user_by_login failed: {e}")
        return
    if user is None:
        logger.warning(f"@{handle_clean}: not found")
        return

    count = 0
    async for tw in api.user_tweets(user.id, limit=limit):
        if count >= limit:
            break
        count += 1
        posted = tw.date if isinstance(tw.date, datetime) else datetime.now(timezone.utc)
        if posted.tzinfo is None:
            posted = posted.replace(tzinfo=timezone.utc)
        yield TweetItem(
            tweet_id=str(tw.id),
            handle=handle_clean,
            text=tw.rawContent or "",
            url=tw.url or f"https://x.com/{handle_clean}/status/{tw.id}",
            posted_at=posted,
        )
