# precam

Solana meme alpha tracker. Watches a curated list of X (Twitter) KOLs, extracts Solana
contract addresses from their tweets, enriches with on-chain + DexScreener data, scores
the find (early-stage bias), and pushes the high-signal ones to Telegram + a web dashboard.

## Stack

- Python 3.11+
- `twscrape` for X scraping (cookie-based accounts pool)
- Helius RPC + DexScreener for token/pair data
- SQLModel + SQLite for storage
- Telegram Bot API for alerts
- FastAPI + Tailwind for the dashboard

## Setup

```bash
# 1. install (uv is fastest; pip works too)
uv venv && source .venv/bin/activate
uv pip install -e .
# or: pip install -e .

# 2. config
cp .env.example .env
# fill: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, HELIUS_API_KEY

# 3. init db
precam init

# 4. add at least one X account so twscrape can fetch tweets
#    (a throwaway account is recommended; cookies persist locally)
precam twitter add <user> <password> <email> <email_password>
precam twitter login

# 5. seed KOLs (edit data/kols.yaml first)
precam kol seed

# 6. one-shot test
precam scan

# 7. run continuously
precam worker        # in one shell
precam dashboard     # in another (http://localhost:8000)
```

## CLI reference

```
precam init                        # create db + dirs
precam scan                        # one pass over all KOLs
precam worker                      # twitter loop
precam dashboard                   # web ui (http://localhost:8000)
precam signals [--limit N] [--early-only]

# Twitter KOL tracker
precam kol add <handle> [--weight N] [--notes "..."]
precam kol remove <handle>
precam kol list
precam kol seed [PATH]             # default: data/kols.yaml

precam twitter add <u> <p> <email> <epw>
precam twitter login
precam twitter status

# Smart wallet tracker (on-chain)
precam wallet discover [--top-pools N] [--pages N] [--min-buys N]
precam wallet refresh [ADDRESS] [--limit N] [--pages N]
precam wallet rank [--by expectancy|win_rate|pnl] [--min-closed N]
precam wallet watch <address>
precam wallet unwatch <address>
precam wallet auto-watch [--top N] [--min-closed N] [--min-expectancy N]
precam wallet list

# Real-time smart-money alerts
precam watcher [--once]

# Pump.fun new-mint scanner
precam pump listen                            # ws stream of new mints
precam pump rescore [--loop] [--interval N] [--batch N]
precam pump list [--clean-only] [--max-risk N]

# Backtest stored signals against TP/SL strategies
precam backtest run <signal|wallet> [--tp 100] [--sl -30] [--max-hold 720] [--limit 100]
precam backtest runs
precam backtest leaderboard <run_id> [--min-trades 3]

# Auto-tune from backtest results (always dry-run by default; pass --apply to commit)
precam autotune kol <run_id> [--alpha 0.5] [--min-closed 5] [--apply]
precam autotune prune-watch <run_id> [--min-expectancy 5] [--apply]

# Paper-trade: live simulator with slippage + fees
precam paper init [--balance 1000] [--force]
precam paper open                                 # one-shot: open new positions
precam paper manage                               # one-shot: close on TP/SL/timeout
precam paper loop                                 # both forever
precam paper status
precam paper positions [--status open|closed|all]
precam paper leaderboard [--by source_key|source_kind]

# Helius webhooks (push-based smart-money tracking, replaces polling)
precam webhook sync [--base-url URL] [--auth SECRET]
precam webhook list [--no-precam-only]
precam webhook delete <id> | --all
```

## Helius webhooks (push, replaces polling)

The default `precam watcher` polls Helius for each watched wallet every
`WATCHER_INTERVAL` seconds. That works but burns credits proportional to
the number of wallets × polls/day. Webhooks flip the model: Helius
calls *us* whenever any watched wallet appears in a swap.

```bash
# 1. expose the dashboard publicly (HTTPS required by Helius)
#    quick path for local dev:
cloudflared tunnel --url http://localhost:8000
# or:
ngrok http 8000

# 2. set in .env (no trailing slash)
echo "WEBHOOK_PUBLIC_URL=https://abc-123.trycloudflare.com" >> .env
echo "WEBHOOK_SECRET=$(openssl rand -hex 16)" >> .env

# 3. register hooks for every is_watched=True wallet
precam webhook sync
#  -> creates / updates / deletes Helius webhooks to match the local watch list
#  -> when you precam wallet watch/unwatch later, re-run sync

# 4. just run the dashboard — it owns the /webhooks/helius endpoint
precam dashboard
# (you can now stop `precam watcher`; webhooks replace polling)
```

Helius posts batches of parsed SWAP transactions to `/webhooks/helius` with
the `WEBHOOK_SECRET` value in the `Authorization` header. The endpoint
validates the header, persists each trade, runs the same convergence
check + Telegram alert flow as the polling watcher.

## Paper trade (live forward test)

Where backtest replays history, paper trade rides on top of the live
signal pipeline:

```bash
precam paper init --balance 1000
precam paper loop                           # one terminal — runs forever
precam worker                               # KOL signal flow
precam watcher                              # smart-money flow
precam dashboard                            # http://localhost:8000/paper
```

Each tick the paper loop:
1. Picks unprocessed Signals (must be `is_early=True` + liq above threshold)
   and unprocessed watched-wallet BUY Trades (above `WATCHER_MIN_BUY_USD`).
2. Fetches current DexScreener price; if liquid, fills at
   `price * (1 + slippage_pct)`. Deducts size + fee from cash.
3. For every open position, marks to market each tick. Closes on:
   - `up_pct >= tp_pct`  → exit at TP (with slippage)
   - `up_pct <= sl_pct`  → exit at SL (with slippage)
   - `held >= max_hold_min` → exit at current price (with slippage)
4. Records realized PnL; portfolio's `current_cash_usd` updates with
   proceeds; total fees and slippage accrue separately for diagnostics.

The `/paper` page shows live equity, ROI vs starting balance, open
positions with unrealized PnL, recently closed positions, and a
per-source-key leaderboard.

Limitations vs. live trading: polling is interval-based (default 60s) so
spikes between ticks are invisible. Slippage is flat percentage —
real impact depends on pool depth. Adjust per-portfolio in `init`.

## Auto-tune (closing the loop)

After a backtest, let the system update its own configuration:

```bash
# 1. backtest the last 200 KOL signals
precam backtest run signal --tp 100 --sl -30 --max-hold 720 --limit 200
#    -> run #N

# 2. preview KOL weight changes (dry-run by default)
precam autotune kol N
#    -> table of old → new weights per KOL

# 3. commit if it looks right
precam autotune kol N --apply

# 4. same loop for the smart-money watch list
precam backtest run wallet --tp 200 --sl -40 --max-hold 1440 --limit 200
precam autotune prune-watch <new_run_id> --apply
```

Smoothing keeps the system stable: with `--alpha 0.5` (default), each tune
moves the weight halfway from the current value to the suggested bucket
(`exp>=+50%`→3.0, +20-50%→2.0, +5-20%→1.0, 0-5%→0.5, <0→0.1). Run after each
backtest; weights drift toward "what actually printed money" instead of vibes.

Safety: `prune-watch` will never unwatch more than `--max-unwatch-fraction`
(default 0.5) of the current list in one pass — worst expectancy goes first.

## Backtesting

Replays stored Signals (KOL alerts) or watched-wallet BUY Trades against a
TP / SL / max-hold strategy using free GeckoTerminal OHLCV (5-min bars, up
to 1000 per request). For each entry:

1. Locate the trading pool (from `dex_url` if known, else GeckoTerminal
   token→pools lookup, highest-liquidity pool wins).
2. Fetch 5-min bars from entry time forward.
3. Walk bars; if a bar's high crosses TP → win at TP price; if low crosses
   SL → loss at SL price; if both in one bar → SL wins (pessimistic).
4. Aggregate per `source_key` (KOL handle or wallet address) → win rate,
   avg/median pnl, expectancy, sum pnl.

Use the leaderboard to identify which KOLs / wallets actually print money
after fees + slippage, and tune `kol.weight` / `--min-expectancy` in
`auto-watch` based on real evidence rather than vibes.

## How the smart wallet tracker works

1. **Discover**: pull GeckoTerminal trending Solana pools → for each pool, fetch recent
   swaps from Helius → record every wallet that bought the base token → wallets that
   appear in ≥ N trending pools are promoted to the watch list.
2. **Refresh**: for each watched wallet, fetch the last ~500 swap txs from Helius,
   normalize into trades, reconstruct positions, compute realized PnL & stats.
3. **Rank** by **expectancy** = (win_rate × avg_win%) − (loss_rate × avg_loss%) — the
   honest meme-trading metric. A trader with 30% win rate but 10x average wins beats a
   90% win rate / +2% trader.

The dashboard at `/wallets` shows the leaderboard; clicking a wallet shows its position
history at `/wallet/<address>`.

## Scoring (KOL signals)

`score = age + liquidity + volume/liquidity + buy-bias + kol-weight` (0-100).
A find is tagged `EARLY` when the pair age is below `EARLY_MAX_AGE_MIN` and
liquidity is above `MIN_LIQUIDITY_USD` (see `.env`).

## Real-time smart-money flow

```bash
# 1. discover candidates from trending pools
precam wallet discover --top-pools 20 --pages 3

# 2. compute expectancy / win-rate for every discovered wallet
precam wallet refresh

# 3. promote top-N qualifiers (by expectancy) to the watch list
precam wallet auto-watch --top 20 --min-closed 5 --min-expectancy 5

# 4. continuous watcher: polls every WATCHER_INTERVAL (default 90s),
#    parses new SWAPs, alerts on BUYS >= WATCHER_MIN_BUY_USD
precam watcher
```

Convergence boost: if N watched wallets buy the same mint within
`WATCHER_CONVERGENCE_WINDOW_MIN` minutes, the Telegram alert flips from
`🎯 SMART BUY` to `🔥 CONVERGENCE` with the count highlighted — that's
the strongest signal in the system.

## Roadmap

- [x] KOL Twitter scanner with CA extraction + Telegram alerts
- [x] Smart-wallet discovery + expectancy-based leaderboard
- [x] Pump.fun new-mint scanner with rug heuristics
- [x] Real-time alerts when a top-ranked wallet opens a new position + convergence boost
- [x] Backtest harness on stored signals (TP/SL/timeout, per-source leaderboard)
- [x] Auto-tune KOL weights & prune watch list from backtest results
- [x] Paper-trade simulator (live signals → virtual portfolio with slippage + fees)
- [x] Helius webhooks (push-based watcher; cuts polling credits ~7×)
- [ ] Discord webhook output
