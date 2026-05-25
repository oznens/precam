import asyncio
from pathlib import Path

import typer
import yaml
from loguru import logger
from sqlalchemy import delete, select

from .config import settings
from .db import Kol, PumpToken, SessionLocal, Signal, Wallet, WalletStat, init_db
from .twitter.scraper import get_api
from .worker import run_forever, scan_once
from .workers.pump import listen as pump_listen, rescore_loop, rescore_pending
from .workers.wallets import discover_from_trending, refresh_all, refresh_wallet

app = typer.Typer(no_args_is_help=True, help="precam — Solana meme alpha tracker")
kol_app = typer.Typer(no_args_is_help=True, help="Manage KOL (key opinion leader) Twitter handles")
tw_app = typer.Typer(no_args_is_help=True, help="Manage twscrape Twitter accounts")
wallet_app = typer.Typer(no_args_is_help=True, help="Smart wallet discovery + ranking")
pump_app = typer.Typer(no_args_is_help=True, help="Pump.fun new-mint scanner + rug scoring")
app.add_typer(kol_app, name="kol")
app.add_typer(tw_app, name="twitter")
app.add_typer(wallet_app, name="wallet")
app.add_typer(pump_app, name="pump")


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


@wallet_app.command("discover")
def wallet_discover(
    top_pools: int = typer.Option(15, help="Number of trending pools to scan"),
    pages: int = typer.Option(3, help="Helius pagination per pool (~100 txs each)"),
    min_buys: int = typer.Option(2, help="Min trending-pool buys to promote a wallet"),
) -> None:
    """Discover candidate smart wallets via trending pools' early buyers."""

    async def _run():
        await init_db()
        n = await discover_from_trending(
            top_pools=top_pools, max_pages_per_pool=pages, min_buys_to_promote=min_buys
        )
        typer.echo(f"added {n} new wallet(s)")

    _arun(_run())


@wallet_app.command("refresh")
def wallet_refresh(
    address: str = typer.Argument("", help="Wallet address; omit to refresh all"),
    limit: int = typer.Option(0, help="Cap on number of wallets when refreshing all"),
    pages: int = typer.Option(5, help="Helius pagination depth"),
) -> None:
    """Rebuild trades/positions/stats from on-chain swap history."""

    async def _run():
        await init_db()
        if address:
            await refresh_wallet(address, max_pages=pages)
            typer.echo(f"refreshed {address}")
        else:
            n = await refresh_all(limit=limit or None)
            typer.echo(f"refreshed {n} wallet(s)")

    _arun(_run())


@wallet_app.command("rank")
def wallet_rank(
    limit: int = typer.Option(30, help="How many to print"),
    min_closed: int = typer.Option(5, help="Min closed positions to qualify"),
    by: str = typer.Option("expectancy", help="Sort key: expectancy | win_rate | pnl"),
) -> None:
    """Print the smart-wallet leaderboard sorted by chosen metric."""

    async def _run():
        await init_db()
        async with SessionLocal() as s:
            res = await s.execute(select(WalletStat).where(WalletStat.closed_positions >= min_closed))
            stats = list(res.scalars().all())
        if not stats:
            typer.echo(f"(no wallets with ≥{min_closed} closed positions yet)")
            return
        key = {
            "expectancy": lambda r: r.expectancy,
            "win_rate": lambda r: r.win_rate,
            "pnl": lambda r: r.total_realized_pnl_usd,
        }[by]
        stats.sort(key=key, reverse=True)
        typer.echo(
            f"{'wallet':<46} {'closed':>6} {'win%':>5} {'avg+':>7} {'avg-':>7} "
            f"{'exp%':>7} {'pnl$':>12}"
        )
        for r in stats[:limit]:
            typer.echo(
                f"{r.wallet:<46} {r.closed_positions:>6} "
                f"{r.win_rate*100:>4.0f}% {r.avg_win_pct:>+6.0f}% {r.avg_loss_pct:>+6.0f}% "
                f"{r.expectancy:>+6.1f}% {r.total_realized_pnl_usd:>12,.0f}"
            )

    _arun(_run())


@wallet_app.command("list")
def wallet_list(limit: int = 30) -> None:
    async def _run():
        await init_db()
        async with SessionLocal() as s:
            res = await s.execute(select(Wallet).order_by(Wallet.discovered_at.desc()).limit(limit))
            ws = res.scalars().all()
        if not ws:
            typer.echo("(no wallets)")
            return
        for w in ws:
            last = w.last_refreshed_at.isoformat() if w.last_refreshed_at else "-"
            typer.echo(f"{w.address}  via={w.discovered_via:<10} last={last}  {w.label[:60]}")

    _arun(_run())


@pump_app.command("listen")
def pump_listen_cmd() -> None:
    """Subscribe to PumpPortal websocket and persist new tokens forever."""

    async def _run():
        await init_db()
        await pump_listen()

    _arun(_run())


@pump_app.command("rescore")
def pump_rescore_cmd(
    batch: int = typer.Option(25, help="Number of tokens to (re)score in one pass"),
    loop: bool = typer.Option(False, help="Run forever every --interval seconds"),
    interval: int = typer.Option(60, help="Sleep between passes when --loop"),
) -> None:
    """Compute rug heuristics for unscored / stalest pump tokens, alert on clean ones."""

    async def _run():
        await init_db()
        if loop:
            await rescore_loop(interval=interval)
        else:
            n = await rescore_pending(batch=batch)
            typer.echo(f"alerted {n}")

    _arun(_run())


@pump_app.command("list")
def pump_list(
    limit: int = 30,
    clean_only: bool = typer.Option(False, "--clean-only", help="Only is_clean tokens"),
    max_risk: float = typer.Option(100.0, help="Filter rug_risk <= this"),
) -> None:
    async def _run():
        await init_db()
        async with SessionLocal() as s:
            q = select(PumpToken).order_by(PumpToken.created_at.desc()).limit(limit)
            if clean_only:
                q = (
                    select(PumpToken)
                    .where(PumpToken.is_clean == True)  # noqa: E712
                    .order_by(PumpToken.created_at.desc())
                    .limit(limit)
                )
            res = await s.execute(q)
            rows = [r for r in res.scalars().all() if r.rug_risk <= max_risk]
        if not rows:
            typer.echo("(no pump tokens)")
            return
        typer.echo(f"{'created':<16} {'sym':<10} {'risk':>5} {'crt%':>5} {'top10%':>6} {'hld':>4} {'mint'}")
        for r in rows:
            typer.echo(
                f"{r.created_at:%m-%d %H:%M:%S} {(r.symbol or '?'):<10} "
                f"{r.rug_risk:>5.0f} {r.creator_share*100:>4.1f}% "
                f"{r.top10_share*100:>5.1f}% {r.holders_count:>4} {r.mint}"
            )

    _arun(_run())


if __name__ == "__main__":
    app()
