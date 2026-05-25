import asyncio
from pathlib import Path

import typer
import yaml
from loguru import logger
from sqlalchemy import delete, select

from .config import settings
from .db import Kol, SessionLocal, Signal, init_db
from .twitter.scraper import get_api
from .worker import run_forever, scan_once

app = typer.Typer(no_args_is_help=True, help="precam — Solana meme alpha tracker")
kol_app = typer.Typer(no_args_is_help=True, help="Manage KOL (key opinion leader) Twitter handles")
tw_app = typer.Typer(no_args_is_help=True, help="Manage twscrape Twitter accounts")
app.add_typer(kol_app, name="kol")
app.add_typer(tw_app, name="twitter")


def _arun(coro):
    return asyncio.run(coro)


@app.command()
def init() -> None:
    """Create DB tables and runtime directories."""
    _arun(init_db())
    typer.echo(f"DB initialised at {settings.database_url}")


@app.command()
def scan() -> None:
    """Run a single scan pass across all KOLs."""

    async def _run():
        await init_db()
        n = await scan_once()
        typer.echo(f"scan complete; {n} new signal(s)")

    _arun(_run())


@app.command()
def worker() -> None:
    """Run the continuous scan loop."""
    _arun(run_forever())


@app.command()
def dashboard() -> None:
    """Start the FastAPI dashboard."""
    import uvicorn

    uvicorn.run(
        "precam.dashboard:app",
        host=settings.dashboard_host,
        port=settings.dashboard_port,
        reload=False,
    )


@kol_app.command("add")
def kol_add(handle: str, weight: float = 1.0, notes: str = "") -> None:
    async def _run():
        await init_db()
        async with SessionLocal() as s:
            handle_clean = handle.lstrip("@").lower()
            res = await s.execute(select(Kol).where(Kol.handle == handle_clean))
            existing = res.scalar_one_or_none()
            if existing:
                existing.weight = weight
                existing.notes = notes
                s.add(existing)
                msg = f"updated @{handle_clean}"
            else:
                s.add(Kol(handle=handle_clean, weight=weight, notes=notes))
                msg = f"added @{handle_clean}"
            await s.commit()
            typer.echo(msg)

    _arun(_run())


@kol_app.command("remove")
def kol_remove(handle: str) -> None:
    async def _run():
        await init_db()
        async with SessionLocal() as s:
            await s.execute(delete(Kol).where(Kol.handle == handle.lstrip("@").lower()))
            await s.commit()
            typer.echo(f"removed @{handle}")

    _arun(_run())


@kol_app.command("list")
def kol_list() -> None:
    async def _run():
        await init_db()
        async with SessionLocal() as s:
            res = await s.execute(select(Kol).order_by(Kol.weight.desc()))
            kols = res.scalars().all()
        if not kols:
            typer.echo("(no KOLs)")
            return
        for k in kols:
            last = k.last_checked_at.isoformat() if k.last_checked_at else "-"
            typer.echo(f"@{k.handle}\tw={k.weight}\tlast={last}\t{k.notes}")

    _arun(_run())


@kol_app.command("seed")
def kol_seed(path: Path = Path("data/kols.yaml")) -> None:
    """Seed KOLs from a YAML file (list of {handle, weight, notes})."""

    async def _run():
        await init_db()
        if not path.exists():
            typer.echo(f"not found: {path}", err=True)
            raise typer.Exit(code=1)
        data = yaml.safe_load(path.read_text()) or []
        added = 0
        async with SessionLocal() as s:
            for item in data:
                handle = item["handle"].lstrip("@").lower()
                res = await s.execute(select(Kol).where(Kol.handle == handle))
                if res.scalar_one_or_none():
                    continue
                s.add(
                    Kol(
                        handle=handle,
                        weight=float(item.get("weight", 1.0)),
                        notes=item.get("notes", ""),
                    )
                )
                added += 1
            await s.commit()
        typer.echo(f"seeded {added} KOL(s) from {path}")

    _arun(_run())


@tw_app.command("add")
def twitter_add(
    username: str,
    password: str,
    email: str,
    email_password: str,
    cookies: str = typer.Option("", help="Optional cookies string (skips login)"),
) -> None:
    """Register a Twitter/X account for twscrape."""

    async def _run():
        api = await get_api()
        await api.pool.add_account(username, password, email, email_password, cookies=cookies)
        typer.echo(f"added @{username}")

    _arun(_run())


@tw_app.command("login")
def twitter_login() -> None:
    """Log in all registered twscrape accounts (one-time, populates cookies)."""

    async def _run():
        api = await get_api()
        await api.pool.login_all()
        typer.echo("login_all completed")

    _arun(_run())


@tw_app.command("status")
def twitter_status() -> None:
    async def _run():
        api = await get_api()
        info = await api.pool.accounts_info()
        for row in info:
            typer.echo(row)

    _arun(_run())


@app.command()
def signals(limit: int = 20, early_only: bool = False) -> None:
    """Print most recent signals."""

    async def _run():
        await init_db()
        async with SessionLocal() as s:
            q = select(Signal).order_by(Signal.created_at.desc()).limit(limit)
            if early_only:
                q = (
                    select(Signal)
                    .where(Signal.is_early == True)  # noqa: E712
                    .order_by(Signal.created_at.desc())
                    .limit(limit)
                )
            res = await s.execute(q)
            rows = res.scalars().all()
        if not rows:
            typer.echo("(no signals yet)")
            return
        for r in rows:
            flag = "EARLY" if r.is_early else "     "
            typer.echo(
                f"{r.created_at:%m-%d %H:%M} {flag} @{r.handle:<18} "
                f"{r.symbol:<8} score={r.score:5.1f} liq=${r.liquidity_usd:>10,.0f} {r.mint}"
            )

    _arun(_run())


if __name__ == "__main__":
    app()
