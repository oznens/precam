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
precam worker                      # loop forever
precam dashboard                   # web ui

precam kol add <handle> [--weight N] [--notes "..."]
precam kol remove <handle>
precam kol list
precam kol seed [PATH]             # default: data/kols.yaml

precam twitter add <u> <p> <email> <epw>
precam twitter login
precam twitter status

precam signals [--limit N] [--early-only]
```

## Scoring

`score = age + liquidity + volume/liquidity + buy-bias + kol-weight` (0-100).
A find is tagged `EARLY` when the pair age is below `EARLY_MAX_AGE_MIN` and
liquidity is above `MIN_LIQUIDITY_USD` (see `.env`).

## Roadmap

- [ ] Smart-wallet tracker (cluster wallets by historical PnL via Helius enhanced txs)
- [ ] Pump.fun new-mint scanner with rug heuristics (dev holdings, LP burn)
- [ ] Backtest harness on stored signals
- [ ] Discord webhook output
- [ ] Paper-trade simulator
