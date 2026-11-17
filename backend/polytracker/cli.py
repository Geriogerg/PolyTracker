"""Command line entry points.

  python -m polytracker.cli api            # HTTP API (uvicorn)
  python -m polytracker.cli worker         # ingestion + analysis worker
  python -m polytracker.cli seed-demo      # load the synthetic dataset
  python -m polytracker.cli analyze        # run one analysis pass now
  python -m polytracker.cli replay <slug|0xconditionId> [...]
                                           # backfill past markets and analyse them
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time


def _logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)


def cmd_api(args) -> None:
    import uvicorn

    uvicorn.run("polytracker.api.main:app", host=args.host, port=args.port, log_level="info",
                proxy_headers=True, forwarded_allow_ips="*")


def cmd_worker(_args) -> None:
    from .workers.main import main

    main()


def cmd_seed(_args) -> None:
    from .analysis.pipeline import run_analysis
    from .db import init_db
    from .demo.seed import seed_demo

    init_db()
    print(seed_demo())
    print("analysis:", {k: v for k, v in run_analysis().items() if k not in ("top_clusters", "top_wallets")})


def cmd_analyze(_args) -> None:
    from .analysis.pipeline import run_analysis
    from .db import init_db

    init_db()
    r = run_analysis()
    print({k: v for k, v in r.items() if k not in ("top_wallets",)})


def cmd_replay(args) -> None:
    """Validate the detector on known past markets (e.g. the Feb 2026 Iran strike)."""
    from sqlalchemy import select

    from .analysis.pipeline import run_analysis
    from .clients.http import Http
    from .clients.normalize import normalize_market
    from .clients.onchain import ChainClient
    from .clients.polymarket import PolymarketClient
    from .db import init_db, insert_ignore, session_scope, upsert
    from .models import Cluster, Market, PricePoint, WalletScore
    from .workers.main import current_config
    from .workers.store import store_trades
    from .workers.tracer import trace_cycle

    init_db()

    async def go():
        http = Http(default_rps=5)
        pm = PolymarketClient(http)
        cfg = current_config()
        cids = []
        for ref in args.markets:
            raw = await pm.market_by_ref(ref)
            if not raw:
                print(f"! market not found: {ref}")
                continue
            r = normalize_market(raw)
            r.update(tracked=True, updated_at=int(time.time()))
            with session_scope() as s:
                upsert(s, Market, [r], ["condition_id"])
            cids.append(r["condition_id"])
            print(f"- {r['question']}  (resolved={r['resolved']}, winner={r['winning_index']})")
            n = 0
            async for page in pm.iter_trades(conditions=[r["condition_id"]], max_pages=args.pages,
                                             source="backfill", taker_only=False):
                n += len(store_trades(page, float(cfg["ingestion"]["min_trade_usd"])))
            print(f"  {n} trades stored")
            token = (r["token_ids"] or [None])[0]
            if token:
                end = r.get("closed_ts") or int(time.time())
                pts = await pm.price_history(token, end - args.days * 86400, end, 10)
                with session_scope() as s:
                    insert_ignore(s, PricePoint, [{"token_id": token, "ts": t, "price": p} for t, p in pts],
                                  ["token_id", "ts"])
                print(f"  {len(pts)} price points")
        oc = cfg["onchain"]
        chain = ChainClient(http, oc["provider"], int(oc["chain_id"]), float(oc["requests_per_second"]))
        if chain.enabled:
            for _ in range(args.trace_cycles):
                st = await trace_cycle(chain, cfg)
                print("  tracer:", st)
                if not st.get("n_wallets_traced"):
                    break
        else:
            print("  (no on-chain API key: skipping funding trace)")
        await http.aclose()
        return cids

    cids = asyncio.run(go())
    cfg = current_config()
    cfg["analysis"]["lookback_days"] = max(cfg["analysis"]["lookback_days"], args.lookback)
    r = run_analysis(cfg)
    print(f"\nanalysis: {r['wallets_scored']} wallets, {r['clusters']} clusters, {r['jumps']} jumps")
    with session_scope() as s:
        cl = s.scalars(select(Cluster).where(Cluster.run_id == r["run_id"]).order_by(Cluster.score.desc()).limit(5))
        print("\nTop clusters:")
        for c in cl:
            print(f"  {c.score:5.1f}  {c.size:3d} wallets  {c.key}")
            for comp in c.components:
                if comp["points"]:
                    print(f"         +{comp['points']:4.1f} {comp['label']}: {comp['detail']}")
        ws = s.scalars(select(WalletScore).where(WalletScore.run_id == r["run_id"])
                       .order_by(WalletScore.score.desc()).limit(10))
        print("\nTop wallets:")
        for w in ws:
            st = w.stats
            print(f"  {w.score:5.1f}  {w.wallet}  vol ${st.get('volume', 0):,.0f}  pnl ${st.get('pnl', 0):,.0f}  "
                  f"timing {st.get('timing_hits')}/{st.get('timing_trials')}")


def main(argv=None) -> None:
    _logging()
    p = argparse.ArgumentParser(prog="polytracker")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("api")
    a.add_argument("--host", default="0.0.0.0")
    a.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    a.set_defaults(fn=cmd_api)
    sub.add_parser("worker").set_defaults(fn=cmd_worker)
    sub.add_parser("seed-demo").set_defaults(fn=cmd_seed)
    sub.add_parser("analyze").set_defaults(fn=cmd_analyze)
    r = sub.add_parser("replay")
    r.add_argument("markets", nargs="+", help="market slugs or 0x condition ids")
    r.add_argument("--pages", type=int, default=60, help="max 1000-trade pages per market")
    r.add_argument("--days", type=int, default=60, help="price history window before close")
    r.add_argument("--lookback", type=int, default=1100, help="analysis lookback days")
    r.add_argument("--trace-cycles", type=int, default=3)
    r.set_defaults(fn=cmd_replay)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main(sys.argv[1:])
