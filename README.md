# PolyTracker

Self-hosted forensics for Polymarket. It watches the markets you choose, links wallets that act together (shared funders, transfers between them, shared cash-out addresses, synchronised bets), and scores each wallet and cluster on how unlikely its record is by chance: winning longshots, buying in the hours before price-moving news, betting big from freshly funded wallets.

It reads public data only. It never trades and never tries to put a real name on a wallet.

```
            ┌──────────── worker (asyncio) ─────────────┐
Polymarket  │ market sync · backfill · live poll + RTDS │
 Gamma/Data ├──► trades, markets, price history         │──► Postgres ◄── API (FastAPI) ◄── nginx + React dashboard
 CLOB/RTDS  │ on-chain tracer (Etherscan V2, Polygon)   │                 + SSE live stream
Etherscan ──┤ analysis every 10 min → scores, clusters  │
            │ alerts → Telegram / Discord               │
            └───────────────────────────────────────────┘
```

## Quick start

Requirements: Docker with Compose v2.24+.

```bash
cp .env.example .env
# first look, no network calls: set DEMO_MODE=true in .env
docker compose up -d --build
open http://localhost:8080
```

**Demo mode** loads a synthetic market with planted actors: a 9-wallet insider ring, a trio coordinating without money links, copy-traders who pile in after news, a 12-wallet sybil farm, two skilled traders and 300 ordinary wallets. A simulated live feed keeps it moving. It is the quickest way to see what each screen means.

**Live mode** (`DEMO_MODE=false`, the default):

1. Add a free Etherscan key (`ETHERSCAN_API_KEY`). Without it the app still works, but only behavioural links (co-betting) are found, and wallet age is unknown.
2. Optionally add Telegram (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) or a Discord webhook (`DISCORD_WEBHOOK_URL`) for alerts.
3. `docker compose up -d --build`. The worker syncs the market list, backfills history for each market (newest first), then polls every 15 s and listens on the real-time socket. The first analysis runs as soon as trades exist; give the backfill 10–30 minutes on a fresh install. Progress is on the **Status** page.

To switch between demo and live data, change `DEMO_MODE` and start from a clean database: `docker compose down -v && docker compose up -d`.

## The dashboard

| Page | What it answers |
|---|---|
| **Overview** | Top clusters, a live trade feed, recent news moves with how much was bought just before, the highest-scoring wallets, alerts, score distribution |
| **Clusters** | The link graph. Dots are wallets (size = money wagered, colour = score); lines are evidence, typed: shared funder, direct transfer, shared cash-out, bet together |
| **Cluster page** | Why it scores what it does (one line per component, with the p-value), members, a timeline of their bets against the news, every piece of linking evidence, the money flows |
| **Wallet page** | Score breakdown, positions and P&L, linked wallets, transfers in and out, trade history, links to Polymarket and Polygonscan |
| **Market page** | Price chart with detected moves and flagged trades (click a move to zoom), and for each move, who bought the right side in the hours before |
| **Settings** | Market scope and alert thresholds, applied within a minute without restart |
| **Status** | Worker heartbeats, errors, analysis runs |

## How detection works

### 1. Units

- **Bet**: a wallet's trades on one outcome, same side, merged when less than 60 min apart. One decision split into twenty fills counts once.
- **Position**: per wallet and resolved market, the outcome it put most money on, its average entry price, and whether it won.
- **News move**: a move of ≥ 20 points within 2 h on the price history, whose new level holds afterwards. It is dated from the last calm price before the climb. This stands in for "the moment the news broke", without needing a news feed.

### 2. Links between wallets (with evidence kept for each)

| Link | Rule | Weight |
|---|---|---|
| Direct transfer | collateral moved between two tracked wallets | 12 |
| Shared funder | both funded by the same address, directly or one hop further up | 10 / 6 |
| Shared cash-out | both sent money to the same outside address | 6 |
| Bet together | same market, same side, within 30 min, in a crowd of ≤ 25 wallets, on ≥ 2 distinct markets; rarer co-bets weigh more | ~1.5 per market |
| Similar behaviour | similar betting fingerprint; only reinforces an existing link | 3 |

**Exchanges and bridges are excluded.** Any address linked to ≥ 20 tracked wallets, or with a full page of on-chain history, is treated as a hub. Without this, everyone who deposits from the same exchange would form one giant fake cluster. Protocol contracts (ConditionalTokens, exchanges, the pUSD on-ramp) are listed in `config.yaml`. Clusters are Louvain communities on the weighted graph, and keep their identity across runs.

### 3. Scores (0–100), every point explained

Wallet score:

| Component | Max | Test |
|---|---|---|
| Wins against the odds | 35 | A bet bought at price *p* wins with probability *p* if the buyer has no edge. The wallet's record is a sum of unequal coin flips (Poisson-binomial). Points come from P(this many wins or more). Ten wins on 90¢ favourites is ordinary; five on 12¢ longshots is about 1 in 40,000. |
| Bought before the news | 30 | Betting episodes that landed in the 12 h before a move in their favour, tested against the chance a randomly timed bet would. |
| New wallet, big bet | 15 | A large bet within 72 h of the wallet's first funding (needs on-chain tracing). |
| Size of the right calls | 10 | Money on longshots that paid off or on moves that came. |
| On-chain ties | 10 | Money links to other tracked wallets. |

The p-value maps to points as follows: nothing above p = 0.1, full marks at p ≤ 10⁻⁶.

**Cluster score** tests the group **as a single trader**: one pooled position per market, so nine wallets acting on one tip count once, not nine times. Pooled wins and timing are combined with money-flow links, fresh wallets and member scores.

On the demo data this separates the actors as intended: insider ring 81, behaviour-only trio 40, sybil farm 16, copy-traders and skilled traders 0, crowd median 0. The test suite enforces this.

## Configuration

- `config.yaml`: scope defaults, ingestion cadence, every analysis threshold and weight, on-chain settings. It is mounted into the containers, so restart after editing (`docker compose restart api worker`).
- **Settings page**: market scope and alert thresholds, stored in the database and overriding `config.yaml`.
- `.env`: secrets and deployment (`DEMO_MODE`, API keys, alert channels, `PUBLIC_URL`, `WEB_PORT`).

Scope modes: `tags` (Polymarket tag slugs, e.g. geopolitics, politics, middle-east), `all` (biggest markets), `explicit` (only your list). Keyword include/exclude filters and an always-track list work in every mode. Recently resolved markets stay tracked (`include_closed_days`) because resolutions are what reveal who was right.

## Validate against a real past case

Backfill one or more past markets and see what the detector finds:

```bash
docker compose exec worker python -m polytracker replay <market-slug-or-0xconditionId> [more…]
```

It stores the market, its trades and price history, traces the biggest wallets on-chain (if a key is set), runs the analysis, and prints the top clusters and wallets with their reasons. Use it on the markets from the reported 2026 Iran-strike cases to check the detector before trusting live alerts.

## Data sources

| Source | Used for |
|---|---|
| Gamma API `/events`, `/markets` | market list, tags, outcomes, token ids, resolution |
| Data API `/v2/trades` (keyset cursor; falls back to v1 `/trades`) | trades, **including maker fills**, so limit-order traders aren't invisible |
| RTDS `wss://ws-live-data.polymarket.com` (`activity/trades`) | live trades; a watchdog reconnects when the feed stalls, and polling runs alongside |
| CLOB `/prices-history` | price series for news-move detection |
| Etherscan V2 `tokentx` (chain 137) or Blockscout | pUSD, USDC.e and USDC transfers for funding and cash-out links |

Polymarket moved collateral from USDC.e to pUSD with CLOB V2 (April 2026); both are traced. If Polymarket changes an endpoint, the Status page shows the error. Base URLs can be overridden with `POLYMARKET_GAMMA_URL`, `POLYMARKET_DATA_URL`, `POLYMARKET_CLOB_URL`, `POLYMARKET_RTDS_URL` and `ONCHAIN_API_URL`.

**Volume and rate limits.** Backfill reads up to `backfill_max_pages` × 1,000 trades per market, newest first, so the busiest markets may not be backfilled to their first day. Free Etherscan keys allow about 5 calls/s; the tracer stays under that and traces the biggest wallets first.

## API

JSON under `/api`: `overview`, `clusters`, `clusters/{key}`, `graph`, `wallets`, `wallets/{address}`, `markets`, `markets/{conditionId}`, `feed`, `alerts`, `settings` (GET/PUT/DELETE), `status`, `analysis/run` (POST), plus `stream` (server-sent events: `trade`, `alert`, `run`). Interactive docs: http://localhost:8080/api/docs.

## Development without Docker

```bash
# backend (needs a Postgres, or use sqlite:///polytracker.db for a quick try)
cd backend && pip install -r requirements.txt
export DATABASE_URL=sqlite:///polytracker.db POLYTRACKER_CONFIG=../config.yaml
python -m polytracker seed-demo        # optional: demo data
python -m polytracker api &            # :8000
python -m polytracker worker           # set DEMO_MODE=true for the simulated feed

# frontend
cd frontend && npm install && npm run dev   # :5173, proxies /api to :8000

# tests (32, ~15 s): statistics, jump detection, normalisation, mocked
# ingestion incl. v2 cursor paging, v1 fallback and the websocket, detection
# separation on the demo data, API contract
cd backend && python -m pytest -q
```

## Limits and responsible use

- **A score is a statistical red flag, not proof.** Coincidences happen across thousands of wallets, sharp analysts exist, and a cluster may be one person with several wallets rather than several people sharing a tip. Look at the evidence on each page before drawing conclusions, and don't publish accusations against identifiable people on the strength of a score.
- On-chain links stop at exchanges and bridges by design. Funds routed through them break the trail.
- Wallet age needs on-chain tracing; without an API key the "new wallet" component stays at zero.
- News moves are inferred from prices. A slow leak with no sharp move won't register as one.
- Polymarket is not authorised for betting in France (ANJ). PolyTracker only reads public data and places no bets.
