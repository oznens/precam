import asyncio
from datetime import datetime
from pathlib import Path

import typer
import yaml
from loguru import logger
from sqlalchemy import delete, select

from .config import settings
from .db import (
    BacktestRun,
    Kol,
    PaperPortfolio,
    PaperPosition,
    PumpToken,
    SessionLocal,
    Signal,
    Wallet,
    WalletStat,
    init_db,
)
from .twitter.scraper import get_api
from .worker import run_forever, scan_once
from .solana.webhooks import (
    delete_webhook,
    find_precam_hooks,
    list_webhooks,
    sync_watched_addresses,
)
from .analytics.convergence import find_convergence
from .workers.cobuyers import find_cobuyers
from .workers.autotune import autotune_kol_weights, prune_watchlist
from .workers.backtest import leaderboard as backtest_leaderboard, run_backtest
from .workers.paper import (
    get_or_init_portfolio,
    manage_positions,
    open_new_positions,
    paper_loop,
    reset_portfolio,
)
from .workers.pump import listen as pump_listen, rescore_loop, rescore_pending
from .workers.wallets import (
    audit_wallets,
    discover_from_graduated,
    discover_from_trending,
    refresh_all,
    refresh_wallet,
)
from .workers.watcher import auto_watch_top, watch_forever, watch_once

app = typer.Typer(no_args_is_help=True, help="precam — Solana meme alpha tracker")
kol_app = typer.Typer(no_args_is_help=True, help="Manage KOL (key opinion leader) Twitter handles")
tw_app = typer.Typer(no_args_is_help=True, help="Manage twscrape Twitter accounts")
wallet_app = typer.Typer(no_args_is_help=True, help="Smart wallet discovery + ranking")
pump_app = typer.Typer(no_args_is_help=True, help="Pump.fun new-mint scanner + rug scoring")
backtest_app = typer.Typer(no_args_is_help=True, help="Backtest stored signals against TP/SL strategies")
autotune_app = typer.Typer(no_args_is_help=True, help="Auto-tune KOL weights + prune watch list from backtest results")
paper_app = typer.Typer(no_args_is_help=True, help="Paper-trade simulator: virtual portfolio against live signals")
webhook_app = typer.Typer(no_args_is_help=True, help="Helius webhooks: push-based smart-money watcher")
app.add_typer(kol_app, name="kol")
app.add_typer(tw_app, name="twitter")
app.add_typer(wallet_app, name="wallet")
app.add_typer(pump_app, name="pump")
app.add_typer(backtest_app, name="backtest")
app.add_typer(autotune_app, name="autotune")
app.add_typer(paper_app, name="paper")
app.add_typer(webhook_app, name="webhook")


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
def watcher(
    once: bool = typer.Option(False, "--once", help="Run a single pass then exit"),
) -> None:
    """Real-time loop: poll watched wallets, alert on smart-money buys."""

    async def _run():
        await init_db()
        if once:
            n = await watch_once()
            typer.echo(f"sent {n} alert(s)")
        else:
            await watch_forever()

    _arun(_run())


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
    min_trade_size: float = typer.Option(
        200.0, help="Ignore buys smaller than this USD (filters sniper bots)"
    ),
) -> None:
    """Discover candidate smart wallets via trending pools' early buyers."""

    async def _run():
        await init_db()
        n = await discover_from_trending(
            top_pools=top_pools,
            max_pages_per_pool=pages,
            min_buys_to_promote=min_buys,
            min_trade_size_usd=min_trade_size,
        )
        typer.echo(f"added {n} new wallet(s)")

    _arun(_run())


@wallet_app.command("discover-graduated")
def wallet_discover_graduated(
    top_pools: int = typer.Option(30, help="Number of graduated pools to scan"),
    pages: int = typer.Option(2, help="Helius pagination per pool (~100 txs each)"),
    min_buys: int = typer.Option(2, help="Min graduated-pool buys to promote a wallet"),
    min_trade_size: float = typer.Option(
        200.0, help="Ignore buys smaller than this USD (filters sniper bots)"
    ),
    max_hours: float = typer.Option(24.0, help="Only pools created within this many hours"),
    min_reserve: float = typer.Option(
        20000.0, help="Only pools with at least this USD liquidity (skip dust)"
    ),
) -> None:
    """Discover smart wallets via newly-graduated Pump.fun pools (Raydium / pump-amm)."""

    async def _run():
        await init_db()
        n = await discover_from_graduated(
            top_pools=top_pools, max_pages_per_pool=pages,
            min_buys_to_promote=min_buys, min_trade_size_usd=min_trade_size,
            max_age_hours=max_hours, min_reserve_usd=min_reserve,
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
            ok, fail = await refresh_all(limit=limit or None)
            typer.echo(f"refreshed {ok} ok / {fail} failed")

    _arun(_run())


@wallet_app.command("rank")
def wallet_rank(
    limit: int = typer.Option(30, help="How many to print"),
    min_closed: int = typer.Option(5, help="Min closed positions to qualify"),
    by: str = typer.Option("expectancy", help="Sort key: expectancy | win_rate | pnl"),
    include_bots: bool = typer.Option(
        False, "--include-bots", help="Include is_likely_bot wallets (default: hide)"
    ),
) -> None:
    """Print the smart-wallet leaderboard sorted by chosen metric."""

    async def _run():
        await init_db()
        async with SessionLocal() as s:
            q = select(WalletStat).where(WalletStat.closed_positions >= min_closed)
            if not include_bots:
                q = q.where(WalletStat.is_likely_bot == False)  # noqa: E712
            res = await s.execute(q)
            stats = list(res.scalars().all())
        if not stats:
            typer.echo(
                f"(no qualifying wallets — try --include-bots or lower --min-closed)"
            )
            return
        key = {
            "expectancy": lambda r: r.expectancy,
            "win_rate": lambda r: r.win_rate,
            "pnl": lambda r: r.total_realized_pnl_usd,
        }[by]
        stats.sort(key=key, reverse=True)
        typer.echo(
            f"{'wallet':<46} {'cls':>4} {'win%':>4} {'exp%':>7} "
            f"{'pnl$':>9} {'size$':>7} {'hold':>6} {'bot'}"
        )
        for r in stats[:limit]:
            bot = "🤖" if r.is_likely_bot else ""
            hold = f"{r.avg_hold_minutes:.0f}m" if r.avg_hold_minutes < 60 else f"{r.avg_hold_minutes/60:.1f}h"
            typer.echo(
                f"{r.wallet:<46} {r.closed_positions:>4} "
                f"{r.win_rate*100:>3.0f}% {r.expectancy:>+6.1f}% "
                f"{r.total_realized_pnl_usd:>9,.0f} {r.avg_trade_size_usd:>7,.0f} "
                f"{hold:>6} {bot}"
            )

    _arun(_run())


@wallet_app.command("watch")
def wallet_watch(address: str) -> None:
    """Flag a wallet for real-time monitoring (sends Telegram alerts on buys)."""

    async def _run():
        await init_db()
        async with SessionLocal() as s:
            res = await s.execute(select(Wallet).where(Wallet.address == address))
            w = res.scalar_one_or_none()
            if w is None:
                s.add(
                    Wallet(
                        address=address,
                        label="manual",
                        discovered_via="manual",
                        is_watched=True,
                        watched_at=datetime.utcnow(),
                    )
                )
                typer.echo(f"added + watched {address}")
            else:
                w.is_watched = True
                w.watched_at = datetime.utcnow()
                s.add(w)
                typer.echo(f"watching {address}")
            await s.commit()

    _arun(_run())


@wallet_app.command("audit")
def wallet_audit(
    only_watched: bool = typer.Option(True, "--watched/--all", help="Audit only is_watched=True"),
    auto_unwatch: bool = typer.Option(False, help="Demote rugged-heavy wallets to is_watched=False"),
    ratio: float = typer.Option(2.0, help="Rugged/realized ratio threshold to flag a wallet"),
    min_rugged: float = typer.Option(1000.0, help="Min rugged USD to consider flagging"),
) -> None:
    """Audit open positions against DexScreener — surface rugged exposure."""

    async def _run():
        await init_db()
        rows = await audit_wallets(
            only_watched=only_watched,
            auto_unwatch=auto_unwatch,
            rugged_to_realized_ratio=ratio,
            min_open_usd=min_rugged,
        )
        rows.sort(key=lambda r: r["open_rugged_usd"], reverse=True)
        typer.echo(
            f"{'wallet':<46} {'realized$':>10} {'live#':>6} {'live$':>9} "
            f"{'rug#':>5} {'rugged$':>10} {'flag'}"
        )
        for r in rows:
            mark = " 🚩 RUG" if r["flagged"] else ""
            typer.echo(
                f"{r['address']:<46} {r['realized_pnl']:>10,.0f} "
                f"{r['open_live_n']:>6} {r['open_live_value_usd']:>9,.0f} "
                f"{r['open_rugged_n']:>5} {r['open_rugged_usd']:>10,.0f}{mark}"
            )
        flagged_n = sum(1 for r in rows if r["flagged"])
        action = "auto-unwatched" if auto_unwatch else "would demote"
        typer.echo(f"\n{flagged_n} flagged ({action} if --auto-unwatch)")

    _arun(_run())


@wallet_app.command("convergence")
def wallet_convergence(
    hours: float = typer.Option(24.0, help="Look-back window in hours"),
    min_wallets: int = typer.Option(2, help="Min distinct wallets buying same mint"),
    only_watched: bool = typer.Option(True, "--watched/--all", help="Restrict to is_watched"),
    limit: int = typer.Option(20, help="Max rows to print"),
) -> None:
    """Surface mints that >= min-wallets bought inside the window — alpha overlap."""

    async def _run():
        await init_db()
        rows = await find_convergence(
            window_hours=hours, min_wallets=min_wallets, only_watched=only_watched
        )
        if not rows:
            scope = "watched" if only_watched else "any"
            typer.echo(f"(no convergence among {scope} wallets in last {hours:g}h)")
            return
        typer.echo(
            f"{'mint':<44} {'n':>3} {'total$':>9} {'spread':>9} {'first_buy(UTC)':<19}"
        )
        for r in rows[:limit]:
            spread = r["spread_minutes"]
            spread_s = f"{spread:.0f}m" if spread < 120 else f"{spread/60:.1f}h"
            typer.echo(
                f"{r['mint']:<44} {r['n_wallets']:>3} {r['total_usd']:>9,.0f} "
                f"{spread_s:>9} {r['first_buy'].strftime('%Y-%m-%d %H:%M:%S')}"
            )
            for w in r["wallets"]:
                typer.echo(
                    f"    {w['address']:<44}  ${w['usd']:>8,.0f}  "
                    f"{w['first_ts'].strftime('%H:%M:%S')}"
                )

    _arun(_run())


@wallet_app.command("cobuyers")
def wallet_cobuyers(
    seed: str = typer.Argument(..., help="Seed wallet to find co-buyers for"),
    days: float = typer.Option(1.0, help="Look-back over the seed's buys"),
    window_minutes: float = typer.Option(30.0, help="Co-buy window around seed's buy time"),
    min_overlap: int = typer.Option(2, help="Min number of seed mints a candidate must share"),
    max_mints: int = typer.Option(20, help="Cap on how many seed mints to scan (API cost)"),
    limit: int = typer.Option(20, help="Max candidates to print"),
) -> None:
    """Find wallets that bought the same mints as SEED inside a tight window."""

    async def _run():
        await init_db()
        result = await find_cobuyers(
            seed_wallet=seed,
            days=days,
            window_minutes=window_minutes,
            min_overlap=min_overlap,
            max_mints=max_mints,
        )
        n_scanned = result["mints_scanned"]
        cands = result["candidates"]
        typer.echo(
            f"seed={seed}  mints_scanned={n_scanned}  window=±{window_minutes:g}m  "
            f"found {len(cands)} candidates"
        )
        if not cands:
            return
        typer.echo(f"\n{'candidate':<46} {'overlap':>7} {'total$':>10}")
        for c in cands[:limit]:
            typer.echo(
                f"{c['address']:<46} {c['n_overlap_mints']:>7} {c['total_usd']:>10,.0f}"
            )
            for h in c["hits"]:
                typer.echo(
                    f"    {h['mint']:<44}  ${h['usd']:>7,.0f}  Δ{h['delta_s']:>5.0f}s  "
                    f"{h['ts'].strftime('%Y-%m-%d %H:%M:%S')}"
                )

    _arun(_run())


@wallet_app.command("unwatch")
def wallet_unwatch(address: str) -> None:
    async def _run():
        await init_db()
        async with SessionLocal() as s:
            res = await s.execute(select(Wallet).where(Wallet.address == address))
            w = res.scalar_one_or_none()
            if w:
                w.is_watched = False
                s.add(w)
                await s.commit()
                typer.echo(f"unwatched {address}")
            else:
                typer.echo("(not found)")

    _arun(_run())


@wallet_app.command("auto-watch")
def wallet_auto_watch(
    top: int = typer.Option(20, help="How many top wallets to promote"),
    min_closed: int = typer.Option(5, help="Min closed positions to qualify"),
    min_expectancy: float = typer.Option(5.0, help="Min expectancy % per trade"),
    include_bots: bool = typer.Option(
        False, "--include-bots", help="Allow is_likely_bot wallets (default: skip)"
    ),
) -> None:
    """Promote top-N qualifying wallets (by expectancy) to the watch list."""

    async def _run():
        await init_db()
        added, total = await auto_watch_top(
            top=top,
            min_closed=min_closed,
            min_expectancy=min_expectancy,
            include_bots=include_bots,
        )
        typer.echo(f"newly watched: {added}  |  total watched: {total}")

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


@backtest_app.command("run")
def backtest_run_cmd(
    source: str = typer.Argument(..., help="signal | wallet"),
    name: str = typer.Option(None, help="Run label (auto if omitted)"),
    tp: float = typer.Option(100.0, help="Take-profit % (e.g. 100 = 2x)"),
    sl: float = typer.Option(-30.0, help="Stop-loss % (e.g. -30)"),
    max_hold: int = typer.Option(720, help="Max hold time (minutes)"),
    limit: int = typer.Option(100, help="How many recent items to simulate"),
    concurrency: int = typer.Option(3, help="Parallel OHLCV fetches"),
) -> None:
    """Run a TP/SL backtest over recent Signals (KOL) or watched-wallet BUY Trades."""

    async def _run():
        await init_db()
        n = name or f"{source}_tp{int(tp)}_sl{int(sl)}_h{max_hold}"
        rid = await run_backtest(
            name=n, source=source, tp_pct=tp, sl_pct=sl,
            max_hold_min=max_hold, limit=limit, concurrency=concurrency,
        )
        typer.echo(f"run #{rid} stored")

    _arun(_run())


@backtest_app.command("runs")
def backtest_runs_cmd(limit: int = 20) -> None:
    async def _run():
        await init_db()
        async with SessionLocal() as s:
            res = await s.execute(
                select(BacktestRun).order_by(BacktestRun.started_at.desc()).limit(limit)
            )
            rows = res.scalars().all()
        if not rows:
            typer.echo("(no runs)")
            return
        typer.echo(
            f"{'id':>4} {'started':<16} {'name':<30} {'trades':>6} {'win%':>5} "
            f"{'exp%':>7} {'avg%':>7} {'sum%':>8}"
        )
        for r in rows:
            typer.echo(
                f"{r.id:>4} {r.started_at:%m-%d %H:%M:%S} {r.name[:30]:<30} "
                f"{r.total_trades:>6} {r.win_rate*100:>4.0f}% "
                f"{r.expectancy:>+6.1f}% {r.avg_pnl_pct:>+6.1f}% {r.sum_pnl_pct:>+7.1f}%"
            )

    _arun(_run())


@backtest_app.command("leaderboard")
def backtest_leaderboard_cmd(
    run_id: int = typer.Argument(..., help="ID from `precam backtest runs`"),
    min_trades: int = typer.Option(3, help="Min closed trades to qualify"),
    limit: int = typer.Option(30, help="Top N rows to print"),
) -> None:
    """Per-source-key (KOL handle / wallet address) leaderboard for one run."""

    async def _run():
        await init_db()
        rows = await backtest_leaderboard(run_id, min_trades=min_trades)
        if not rows:
            typer.echo(f"(no qualifying sources for run {run_id})")
            return
        typer.echo(
            f"{'source_key':<46} {'closed':>6} {'win%':>5} {'avg%':>7} "
            f"{'med%':>7} {'exp%':>7} {'sum%':>8}"
        )
        for r in rows[:limit]:
            typer.echo(
                f"{r['source_key'][:46]:<46} {r['closed']:>6} "
                f"{r['win_rate']*100:>4.0f}% {r['avg_pnl_pct']:>+6.1f}% "
                f"{r['median_pnl_pct']:>+6.1f}% {r['expectancy']:>+6.1f}% {r['sum_pnl_pct']:>+7.1f}%"
            )

    _arun(_run())


@autotune_app.command("kol")
def autotune_kol_cmd(
    run_id: int = typer.Argument(..., help="Backtest run id (precam backtest runs)"),
    alpha: float = typer.Option(0.5, help="Smoothing 0-1 (0=no change, 1=replace)"),
    min_closed: int = typer.Option(5, help="Min closed trades for a KOL to qualify"),
    apply: bool = typer.Option(False, "--apply", help="Actually write changes (default: dry-run)"),
) -> None:
    """Re-weight KOLs from a backtest's leaderboard. Prints diff; --apply commits."""

    async def _run():
        await init_db()
        changes = await autotune_kol_weights(
            run_id, alpha=alpha, min_closed=min_closed, dry_run=not apply
        )
        if not changes:
            typer.echo("(no changes)")
            return
        typer.echo(
            f"{'handle':<24} {'closed':>6} {'exp%':>7} {'sugg':>6} {'old':>6} → {'new':>6} {'action':<8}"
        )
        for c in changes:
            arrow = "→"
            if c.action == "raise":
                color_open, color_close = "\033[32m", "\033[0m"
            elif c.action == "lower":
                color_open, color_close = "\033[31m", "\033[0m"
            else:
                color_open, color_close = "", ""
            typer.echo(
                f"{c.handle[:24]:<24} {c.closed:>6} {c.expectancy_pct:>+6.1f}% "
                f"{c.suggested:>6.2f} {c.old_weight:>6.2f} {arrow} "
                f"{color_open}{c.new_weight:>6.2f}{color_close} {c.action:<8}"
            )
        if not apply:
            typer.echo("\n(dry-run — re-run with --apply to commit)")

    _arun(_run())


@autotune_app.command("prune-watch")
def autotune_prune_watch_cmd(
    run_id: int = typer.Argument(..., help="Backtest run id (precam backtest runs)"),
    min_expectancy: float = typer.Option(5.0, help="Unwatch wallets with exp% below this"),
    min_closed: int = typer.Option(5, help="Min closed trades to make a judgment"),
    max_unwatch_fraction: float = typer.Option(0.5, help="Safety: cap % of list to unwatch"),
    apply: bool = typer.Option(False, "--apply", help="Actually write changes"),
) -> None:
    """Unwatch low-expectancy wallets from the watch list (with a safety cap)."""

    async def _run():
        await init_db()
        changes = await prune_watchlist(
            run_id,
            min_expectancy=min_expectancy,
            min_closed=min_closed,
            max_unwatch_fraction=max_unwatch_fraction,
            dry_run=not apply,
        )
        if not changes:
            typer.echo("(no watched wallets matched this run)")
            return
        typer.echo(
            f"{'wallet':<46} {'closed':>6} {'exp%':>7} {'action':<8} {'label'}"
        )
        for c in changes:
            typer.echo(
                f"{c.address[:46]:<46} {c.closed:>6} {c.expectancy_pct:>+6.1f}% "
                f"{c.action:<8} {c.label[:40]}"
            )
        if not apply:
            typer.echo("\n(dry-run — re-run with --apply to commit)")

    _arun(_run())


@paper_app.command("status")
def paper_status_cmd() -> None:
    """Print the active portfolio's summary."""

    async def _run():
        await init_db()
        p = await get_or_init_portfolio()
        async with SessionLocal() as s:
            opens = list(
                (
                    await s.execute(
                        select(PaperPosition).where(
                            PaperPosition.portfolio_id == p.id,
                            PaperPosition.status == "open",
                        )
                    )
                ).scalars().all()
            )
        open_value = sum(o.last_price * o.entry_tokens for o in opens if o.last_price > 0)
        total_equity = p.current_cash_usd + open_value
        roi = ((total_equity / p.starting_balance_usd) - 1) * 100 if p.starting_balance_usd > 0 else 0
        typer.echo(f"portfolio #{p.id}  starting ${p.starting_balance_usd:.2f}")
        typer.echo(f"  cash               ${p.current_cash_usd:>10,.2f}")
        typer.echo(f"  open positions     {len(opens):>10d}  (live value ${open_value:,.2f})")
        typer.echo(f"  total equity       ${total_equity:>10,.2f}  ({roi:+.1f}% ROI)")
        typer.echo(f"  realized pnl       ${p.total_realized_pnl_usd:>+10,.2f}")
        typer.echo(f"  fees paid          ${p.total_fees_usd:>10,.2f}")
        typer.echo(f"  slippage paid      ${p.total_slippage_usd:>10,.2f}")
        typer.echo(f"  positions: opened={p.positions_opened}  closed={p.positions_closed}")
        typer.echo(
            f"  strategy: tp={p.tp_pct}% sl={p.sl_pct}% hold={p.max_hold_min}m "
            f"slip={p.slippage_pct}% fee=${p.fee_usd}"
        )

    _arun(_run())


@paper_app.command("init")
def paper_init_cmd(
    balance: float = typer.Option(None, help="Starting balance USD (default: env)"),
    force: bool = typer.Option(False, "--force", help="Wipe existing portfolio + positions"),
) -> None:
    """Create or reset the paper portfolio."""

    async def _run():
        await init_db()
        async with SessionLocal() as s:
            existing = (await s.execute(select(PaperPortfolio))).scalars().all()
        if existing and not force:
            typer.echo("portfolio already exists; pass --force to reset", err=True)
            raise typer.Exit(1)
        p = await reset_portfolio(balance=balance)
        typer.echo(f"portfolio #{p.id} ready: ${p.current_cash_usd:.2f}")

    _arun(_run())


@paper_app.command("open")
def paper_open_cmd() -> None:
    """One-shot: scan for new signals/buys and open paper positions."""

    async def _run():
        await init_db()
        n = await open_new_positions()
        typer.echo(f"opened {n} position(s)")

    _arun(_run())


@paper_app.command("manage")
def paper_manage_cmd() -> None:
    """One-shot: mark-to-market open positions and close on TP/SL/timeout."""

    async def _run():
        await init_db()
        n = await manage_positions()
        typer.echo(f"closed {n} position(s)")

    _arun(_run())


@paper_app.command("loop")
def paper_loop_cmd(
    interval: int = typer.Option(0, help="Tick interval seconds (0=env default)"),
) -> None:
    """Continuous loop: open new + manage existing every interval seconds."""

    async def _run():
        await init_db()
        await paper_loop(interval=interval or None)

    _arun(_run())


@paper_app.command("positions")
def paper_positions_cmd(
    status: str = typer.Option("open", help="open | closed | all"),
    limit: int = typer.Option(30, help="How many to print"),
) -> None:
    async def _run():
        await init_db()
        async with SessionLocal() as s:
            q = select(PaperPosition)
            if status == "open":
                q = q.where(PaperPosition.status == "open").order_by(PaperPosition.opened_at.desc())
            elif status == "closed":
                q = q.where(PaperPosition.status == "closed").order_by(PaperPosition.closed_at.desc())
            else:
                q = q.order_by(PaperPosition.opened_at.desc())
            q = q.limit(limit)
            rows = list((await s.execute(q)).scalars().all())
        if not rows:
            typer.echo("(no positions)")
            return
        typer.echo(
            f"{'id':>4} {'opened':<16} {'kind':<7} {'sym':<10} {'status':<7} "
            f"{'entry$':>10} {'now/exit$':>10} {'pnl$':>8} {'pnl%':>7} {'reason':<8}"
        )
        for r in rows:
            now_or_exit = r.exit_price_filled if r.status == "closed" else r.last_price
            pnl = r.realized_pnl_usd if r.status == "closed" else (
                (now_or_exit - r.entry_price_filled) * r.entry_tokens if now_or_exit else 0.0
            )
            pnl_pct = r.realized_pnl_pct if r.status == "closed" else (
                ((now_or_exit / r.entry_price_filled) - 1) * 100 if now_or_exit > 0 else 0.0
            )
            typer.echo(
                f"{r.id:>4} {r.opened_at:%m-%d %H:%M:%S} {r.source_kind:<7} "
                f"{(r.symbol or r.mint[:6]):<10} {r.status:<7} "
                f"${r.entry_price_filled:>9.4g} ${now_or_exit:>9.4g} "
                f"{pnl:>+7.2f} {pnl_pct:>+6.1f}% {r.exit_reason:<8}"
            )

    _arun(_run())


@paper_app.command("leaderboard")
def paper_leaderboard_cmd(
    by: str = typer.Option("source_key", help="source_key | source_kind"),
    min_trades: int = typer.Option(3, help="Min closed trades to qualify"),
    limit: int = typer.Option(30, help="Top N rows"),
) -> None:
    """Per-source-key leaderboard from CLOSED paper positions only."""

    async def _run():
        await init_db()
        async with SessionLocal() as s:
            closed = list(
                (
                    await s.execute(
                        select(PaperPosition).where(PaperPosition.status == "closed")
                    )
                ).scalars().all()
            )
        if not closed:
            typer.echo("(no closed positions yet)")
            return
        groups: dict[str, list[PaperPosition]] = {}
        for p in closed:
            key = p.source_key if by == "source_key" else p.source_kind
            groups.setdefault(key, []).append(p)
        out = []
        for k, items in groups.items():
            n = len(items)
            if n < min_trades:
                continue
            wins = [p for p in items if p.realized_pnl_usd > 0]
            pnls_pct = [p.realized_pnl_pct for p in items]
            avg_win = (
                sum(p.realized_pnl_pct for p in wins) / len(wins) if wins else 0.0
            )
            avg_loss = (
                sum(p.realized_pnl_pct for p in items if p.realized_pnl_pct <= 0)
                / max(n - len(wins), 1)
            )
            win_rate = len(wins) / n
            out.append({
                "key": k,
                "n": n,
                "wins": len(wins),
                "win_rate": win_rate,
                "avg_pnl_pct": sum(pnls_pct) / n,
                "expectancy": win_rate * avg_win + (1 - win_rate) * avg_loss,
                "sum_usd": sum(p.realized_pnl_usd for p in items),
            })
        out.sort(key=lambda r: r["expectancy"], reverse=True)
        typer.echo(
            f"{'key':<46} {'closed':>6} {'win%':>5} {'avg%':>7} {'exp%':>7} {'sum$':>9}"
        )
        for r in out[:limit]:
            typer.echo(
                f"{r['key'][:46]:<46} {r['n']:>6} "
                f"{r['win_rate']*100:>4.0f}% {r['avg_pnl_pct']:>+6.1f}% "
                f"{r['expectancy']:>+6.1f}% {r['sum_usd']:>+8.2f}"
            )

    _arun(_run())


@webhook_app.command("sync")
def webhook_sync_cmd(
    base_url: str = typer.Option(
        "", help="Public base URL of this server (default: WEBHOOK_PUBLIC_URL)"
    ),
    auth: str = typer.Option(
        "", help="Auth header value (default: WEBHOOK_SECRET)"
    ),
) -> None:
    """Reconcile Helius webhooks with the current is_watched=True wallet list.

    Creates / updates / deletes hooks under our base URL so the registered
    addresses always match the local watch list.
    """

    async def _run():
        if not settings.helius_api_key:
            typer.echo("HELIUS_API_KEY required for webhook commands", err=True)
            raise typer.Exit(1)
        await init_db()
        url = base_url or settings.webhook_public_url
        if not url:
            typer.echo(
                "WEBHOOK_PUBLIC_URL is empty — pass --base-url or set it in .env",
                err=True,
            )
            raise typer.Exit(1)
        secret = auth or settings.webhook_secret
        async with SessionLocal() as s:
            watched = list(
                (
                    await s.execute(
                        select(Wallet).where(Wallet.is_watched == True)  # noqa: E712
                    )
                ).scalars().all()
            )
        addresses = [w.address for w in watched]
        summary = await sync_watched_addresses(
            addresses, base_url=url, auth_header=secret
        )
        typer.echo(
            f"target {summary['url']}\n"
            f"  addresses watched: {summary['total_addresses']}\n"
            f"  webhooks created : {summary['created']}\n"
            f"  webhooks updated : {summary['updated']}\n"
            f"  webhooks deleted : {summary['deleted']}"
        )

    _arun(_run())


@webhook_app.command("list")
def webhook_list_cmd(precam_only: bool = typer.Option(True, help="Filter to our base URL")) -> None:
    async def _run():
        if not settings.helius_api_key:
            typer.echo("HELIUS_API_KEY required for webhook commands", err=True)
            raise typer.Exit(1)
        await init_db()
        if precam_only and settings.webhook_public_url:
            base = settings.webhook_public_url.rstrip("/") + "/webhooks/helius"
            hooks = await find_precam_hooks(base)
        else:
            hooks = await list_webhooks()
        if not hooks:
            typer.echo("(no webhooks)")
            return
        for h in hooks:
            hid = h.get("webhookID") or h.get("id") or "?"
            url = h.get("webhookURL") or "?"
            addrs = h.get("accountAddresses") or []
            typer.echo(f"{hid}  {url}  ({len(addrs)} addrs)")

    _arun(_run())


@webhook_app.command("delete")
def webhook_delete_cmd(
    webhook_id: str = typer.Argument("", help="ID to delete; omit for --all"),
    all_: bool = typer.Option(False, "--all", help="Delete every precam-owned hook"),
) -> None:
    async def _run():
        if not settings.helius_api_key:
            typer.echo("HELIUS_API_KEY required for webhook commands", err=True)
            raise typer.Exit(1)
        await init_db()
        if all_:
            base = (settings.webhook_public_url or "").rstrip("/") + "/webhooks/helius"
            hooks = await find_precam_hooks(base) if settings.webhook_public_url else []
            if not hooks:
                typer.echo("(nothing to delete)")
                return
            for h in hooks:
                hid = h.get("webhookID") or h.get("id") or ""
                if hid:
                    await delete_webhook(hid)
                    typer.echo(f"deleted {hid}")
        elif webhook_id:
            await delete_webhook(webhook_id)
            typer.echo(f"deleted {webhook_id}")
        else:
            typer.echo("provide an ID or --all", err=True)
            raise typer.Exit(1)

    _arun(_run())


if __name__ == "__main__":
    app()
