"""One analysis run: load -> jumps -> features -> graph -> communities -> scores -> persist."""
from __future__ import annotations

import hashlib
import logging
import time
from collections import defaultdict

import numpy as np
import pandas as pd
from sqlalchemy import delete, desc, select

from ..config import effective_config
from ..db import config_overrides, insert_ignore, session_scope
from ..models import (
    Address,
    AnalysisRun,
    Cluster,
    Edge,
    Market,
    PricePoint,
    PriceJump,
    Trade,
    Transfer,
    Wallet,
    WalletScore,
)
from .features import TRADE_COLS, build_bets, build_jump_index, build_positions
from .graph import (
    EdgeBook,
    behaviour_reinforcement,
    co_bet_edges,
    communities,
    detect_hubs,
    onchain_edges,
)
from .jumps import Jump, detect_jumps, smooth_trade_prices
from .scoring import STRUCTURAL, score_cluster, score_wallet

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ loading
def _load(cfg: dict) -> dict:
    a = cfg["analysis"]
    since = int(time.time() - a["lookback_days"] * 86400)
    with session_scope() as s:
        conn = s.connection()
        trades = pd.read_sql(
            select(*[getattr(Trade, c) for c in TRADE_COLS]).where(Trade.ts >= since), conn
        )
        markets = {
            m.condition_id: {
                "question": m.question,
                "slug": m.slug,
                "outcomes": m.outcomes or ["Yes", "No"],
                "token_ids": m.token_ids or [],
                "resolved": m.resolved,
                "winning_index": m.winning_index,
            }
            for m in s.scalars(select(Market))
        }
        tokens0 = {m["token_ids"][0]: cid for cid, m in markets.items() if m["token_ids"]}
        prices = pd.read_sql(
            select(PricePoint.token_id, PricePoint.ts, PricePoint.price).where(PricePoint.ts >= since), conn
        )
        prices = prices[prices["token_id"].isin(tokens0.keys())]
        prices["condition_id"] = prices["token_id"].map(tokens0)
        transfers = pd.read_sql(
            select(Transfer.tx_hash, Transfer.from_addr, Transfer.to_addr, Transfer.amount, Transfer.ts), conn
        )
        addr_kinds = {r.address: r.kind for r in s.scalars(select(Address))}
        wallets = {
            w.address: {"first_funded_ts": w.first_funded_ts, "first_trade_ts": w.first_trade_ts}
            for w in s.scalars(select(Wallet))
        }
        prev = s.scalars(select(AnalysisRun).where(AnalysisRun.status == "done").order_by(desc(AnalysisRun.id))).first()
        prev_clusters = (
            [(c.key, set(c.members)) for c in s.scalars(select(Cluster).where(Cluster.run_id == prev.id))]
            if prev
            else []
        )
    for col in ("outcome_index", "ts"):
        if col in trades:
            trades[col] = trades[col].astype("int64")
    return {
        "trades": trades,
        "markets": markets,
        "prices": prices,
        "transfers": transfers,
        "addr_kinds": addr_kinds,
        "wallets": wallets,
        "prev_clusters": prev_clusters,
    }


# -------------------------------------------------------------------- jumps
def find_jumps(trades: pd.DataFrame, prices: pd.DataFrame, cfg: dict) -> list[Jump]:
    a = cfg["analysis"]
    thr, win = float(a["jump_threshold"]), int(a["jump_window_minutes"] * 60)
    out: list[Jump] = []
    priced = set()
    if not prices.empty:
        for m, g in prices.groupby("condition_id"):
            if len(g) >= 10:
                priced.add(m)
                out += detect_jumps(m, g["ts"].to_numpy(), g["price"].to_numpy(dtype=float), thr, win)
    if not trades.empty:
        t = trades[~trades["condition_id"].isin(priced)]
        for m, g in t.groupby("condition_id"):
            if len(g) < 5:
                continue
            g = g.sort_values("ts")
            p0 = np.where(g["outcome_index"].to_numpy() == 0, g["price"].to_numpy(), 1 - g["price"].to_numpy())
            ts = g["ts"].to_numpy()
            out += detect_jumps(m, ts, smooth_trade_prices(ts, p0.astype(float)), thr, win)
    return out


def _cluster_key(members: set[str], prev: list[tuple[str, set[str]]], taken: set[str]) -> str:
    best, best_j = None, 0.0
    for key, pm in prev:
        if key in taken:
            continue
        j = len(members & pm) / len(members | pm)
        if j > best_j:
            best, best_j = key, j
    if best and best_j >= 0.4:
        return best
    return hashlib.sha1(",".join(sorted(members)).encode()).hexdigest()[:12]


# ---------------------------------------------------------------------- run
def run_analysis(cfg: dict | None = None) -> dict:
    t0 = time.time()
    if cfg is None:
        with session_scope() as s:
            cfg = effective_config(config_overrides(s))
    a = cfg["analysis"]
    with session_scope() as s:
        run = AnalysisRun(started_at=int(t0), status="running", stats={})
        s.add(run)
        s.flush()
        run_id = run.id

    try:
        summary = _run(run_id, cfg)
    except Exception as exc:
        log.exception("analysis run %s failed", run_id)
        with session_scope() as s:
            r = s.get(AnalysisRun, run_id)
            r.status, r.error, r.finished_at = "failed", repr(exc)[:2000], int(time.time())
        raise

    with session_scope() as s:
        r = s.get(AnalysisRun, run_id)
        r.status, r.finished_at = "done", int(time.time())
        summary["duration_s"] = round(time.time() - t0, 2)
        r.stats = summary
        # prune old runs (cascade removes their scores/clusters/edges)
        keep = int(a.get("keep_runs", 30))
        old = s.scalars(select(AnalysisRun.id).order_by(desc(AnalysisRun.id)).offset(keep)).all()
        if old:
            for tbl in (WalletScore, Cluster, Edge):
                s.execute(delete(tbl).where(tbl.run_id.in_(old)))
            s.execute(delete(AnalysisRun).where(AnalysisRun.id.in_(old)))
    log.info("analysis run %s done in %.1fs: %s", run_id, time.time() - t0,
             {k: summary[k] for k in ("wallets_scored", "clusters", "edges", "jumps")})
    summary["run_id"] = run_id
    return summary


def _run(run_id: int, cfg: dict) -> dict:
    a = cfg["analysis"]
    oc = cfg["onchain"]
    d = _load(cfg)
    trades, markets = d["trades"], d["markets"]
    pre_s = int(a["pre_jump_hours"] * 3600)

    jumps = find_jumps(trades, d["prices"], cfg)
    bets = build_bets(trades, int(a["bet_merge_minutes"] * 60))
    winners = {m: int(v["winning_index"]) for m, v in markets.items() if v["resolved"] and v["winning_index"] is not None}
    positions = build_positions(trades, winners, float(a["min_position_usd"]))
    jx = build_jump_index(jumps, bets, pre_s)

    tracked = set(trades["wallet"].unique()) if len(trades) else set()
    static_hubs = set(oc["system_addresses"]) | set(oc["known_hubs"]) | {
        addr for addr, k in d["addr_kinds"].items() if k in ("hub", "system")
    }
    hubs = detect_hubs(d["transfers"], tracked, static_hubs, int(a["hub_min_fanout"]))

    book = EdgeBook()
    onchain_edges(book, d["transfers"], tracked, hubs, a["weights"], depth=int(oc["trace_depth"]))
    co_bet_edges(
        book, bets, int(a["co_bet_window_minutes"] * 60), int(a["max_window_wallets"]),
        int(a["min_co_bet_markets"]), float(a["weights"]["co_bet"]),
    )
    behaviour_reinforcement(book, bets, float(a["weights"]["behavior"]))
    G, comms = communities(book, float(a["min_edge_weight"]))

    structural_links: dict[str, int] = defaultdict(int)
    for (u, v), acc in book.edges.items():
        if acc.kinds & STRUCTURAL:
            structural_links[u] += 1
            structural_links[v] += 1

    # -------------------------------------------------------- wallet scores
    n_trades = trades.groupby("wallet").size().to_dict() if len(trades) else {}
    bets_by_w = dict(tuple(bets.groupby("wallet"))) if len(bets) else {}
    pos_by_w = dict(tuple(positions.groupby("wallet"))) if len(positions) else {}
    empty_pos = positions.iloc[0:0]
    in_graph = set(G.nodes())
    wallet_rows, w_scores, w_stats = [], {}, {}
    min_usd = float(a["min_position_usd"])
    for w, wb in bets_by_w.items():
        if w not in in_graph and float(wb["usd"].sum()) < min_usd:
            continue  # dust, not worth a row
        info = d["wallets"].get(w, {})
        score, comps, stats = score_wallet(
            w, wb, pos_by_w.get(w, empty_pos), jx, cfg,
            info.get("first_funded_ts"), info.get("first_trade_ts"),
            n_trades.get(w, 0), structural_links.get(w, 0),
        )
        w_scores[w], w_stats[w] = score, stats
        wallet_rows.append({"run_id": run_id, "wallet": w, "score": score, "cluster_key": None,
                            "components": comps, "stats": _jsonable(stats)})

    # -------------------------------------------------------- clusters
    taken: set[str] = set()
    cluster_rows, member_of = [], {}
    for members in comms:
        key = _cluster_key(set(members), d["prev_clusters"], taken)
        taken.add(key)
        ms = sorted(members, key=lambda w: -w_scores.get(w, 0))
        cb = bets[bets["wallet"].isin(members)] if len(bets) else bets
        cp = positions[positions["wallet"].isin(members)] if len(positions) else positions
        sub_edges = [
            (u, v, {"kinds": sorted(book.edges[(u, v) if u < v else (v, u)].kinds),
                    "evidence": book.edges[(u, v) if u < v else (v, u)].evidence})
            for u, v in G.subgraph(members).edges()
        ]
        score, comps, stats, top = score_cluster(ms, cb, cp, jx, cfg, sub_edges, w_scores, w_stats, markets)
        cluster_rows.append({"run_id": run_id, "key": key, "score": score, "size": len(ms), "members": ms,
                             "components": comps, "stats": _jsonable(stats), "top_markets": top})
        for m in members:
            member_of[m] = key
    for row in wallet_rows:
        row["cluster_key"] = member_of.get(row["wallet"])

    edge_rows = []
    for u, v, data in G.edges(data=True):
        acc = book.edges[(u, v) if u < v else (v, u)]
        a_, b_ = (u, v) if u < v else (v, u)
        edge_rows.append({"run_id": run_id, "a": a_, "b": b_, "weight": round(acc.weight, 3),
                          "kinds": sorted(acc.kinds), "evidence": _jsonable(acc.evidence)})

    with session_scope() as s:
        insert_ignore(s, WalletScore, wallet_rows, ["run_id", "wallet"])
        insert_ignore(s, Cluster, cluster_rows, ["run_id", "key"])
        insert_ignore(s, Edge, edge_rows, ["run_id", "a", "b"])
        s.execute(delete(PriceJump))
        insert_ignore(s, PriceJump, [j.as_row() for j in jumps], ["id"])
        # remember detected hubs so the tracer stops walking through them
        new_hubs = [{"address": h, "kind": "hub"} for h in hubs if d["addr_kinds"].get(h) not in ("hub", "system")]
        if new_hubs:
            from ..db import upsert

            upsert(s, Address, new_hubs, ["address"], ["kind"])

    clusters_sorted = sorted(cluster_rows, key=lambda c: -c["score"])
    return {
        "trades": int(len(trades)),
        "bets": int(len(bets)),
        "wallets_scored": len(wallet_rows),
        "clusters": len(cluster_rows),
        "edges": len(edge_rows),
        "jumps": len(jumps),
        "hubs": len(hubs),
        "top_clusters": [{"key": c["key"], "score": c["score"], "size": c["size"]} for c in clusters_sorted[:20]],
        "top_wallets": sorted(
            ({"wallet": w, "score": s_} for w, s_ in w_scores.items()), key=lambda x: -x["score"]
        )[:20],
    }


def _jsonable(obj):
    """Make numpy scalars/NaN JSON-safe."""
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        return None if np.isnan(f) or np.isinf(f) else f
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj
