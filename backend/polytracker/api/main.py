"""HTTP API for the dashboard (JSON + server-sent events)."""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Literal

import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.orm import Session

from ..config import EDITABLE_SECTIONS, effective_config, env
from ..db import config_overrides, get_setting, init_db, session_scope, set_setting, worker_statuses
from ..models import (
    Address,
    Alert,
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

@asynccontextmanager
async def lifespan(_app):
    init_db()
    yield


app = FastAPI(title="PolyTracker", version="2.0", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ------------------------------------------------------------------ helpers
def latest_run(s: Session) -> AnalysisRun | None:
    return s.scalars(select(AnalysisRun).where(AnalysisRun.status == "done").order_by(desc(AnalysisRun.id))).first()


def _names(s: Session, addrs) -> dict[str, dict]:
    addrs = list(set(addrs))
    if not addrs:
        return {}
    out = {}
    for i in range(0, len(addrs), 1000):
        for w in s.scalars(select(Wallet).where(Wallet.address.in_(addrs[i : i + 1000]))):
            out[w.address] = {"name": w.name or w.pseudonym}
    return out


def _scores(s: Session, run: AnalysisRun | None, addrs) -> dict[str, WalletScore]:
    if not run:
        return {}
    addrs = list(set(addrs))
    out = {}
    for i in range(0, len(addrs), 1000):
        for w in s.scalars(
            select(WalletScore).where(WalletScore.run_id == run.id, WalletScore.wallet.in_(addrs[i : i + 1000]))
        ):
            out[w.wallet] = w
    return out


def _cluster_summary(c: Cluster) -> dict:
    return {
        "key": c.key,
        "score": c.score,
        "size": c.size,
        "volume": c.stats.get("volume", 0),
        "pnl": c.stats.get("pnl", 0),
        "top_market": c.top_markets[0] if c.top_markets else None,
        "components": [{"key": x["key"], "label": x["label"], "points": x["points"], "max": x["max"]}
                       for x in c.components],
        "members": c.members[:6],
        "first_ts": c.stats.get("first_ts"),
        "last_ts": c.stats.get("last_ts"),
        "n_structural": c.stats.get("n_structural", 0),
    }


def _wallet_summary(w: WalletScore, name: str | None) -> dict:
    st = w.stats or {}
    return {
        "wallet": w.wallet,
        "name": name,
        "score": w.score,
        "cluster_key": w.cluster_key,
        "volume": st.get("volume", 0),
        "pnl": st.get("pnl", 0),
        "win_rate": st.get("win_rate"),
        "resolved": st.get("resolved", 0),
        "n_markets": st.get("n_markets", 0),
        "timing_hits": st.get("timing_hits", 0),
        "last_ts": st.get("last_ts"),
        "components": [{"key": x["key"], "points": x["points"], "max": x["max"]} for x in w.components],
    }


def _market_brief(m: Market | None) -> dict | None:
    if m is None:
        return None
    return {"condition_id": m.condition_id, "question": m.question, "slug": m.slug,
            "outcomes": m.outcomes, "resolved": m.resolved, "winning_index": m.winning_index,
            "last_price": m.last_price}


def _alert(a: Alert) -> dict:
    return {"id": a.id, "created_at": a.created_at, "kind": a.kind, "ref": a.ref, "score": a.score,
            "title": a.title, "message": a.message, "channels": a.channels, "acknowledged": a.acknowledged}


def _cfg(s: Session) -> dict:
    return effective_config(config_overrides(s))


# ------------------------------------------------------------------ health
@app.get("/api/health")
def health():
    return {"ok": True, "demo": env.demo_mode, "time": int(time.time())}


# ---------------------------------------------------------------- overview
@app.get("/api/overview")
def overview():
    now = int(time.time())
    with session_scope() as s:
        run = latest_run(s)
        tracked = s.scalar(select(func.count()).select_from(Market).where(Market.tracked)) or 0
        n_trades = s.scalar(select(func.count()).select_from(Trade)) or 0
        n_wallets = s.scalar(select(func.count()).select_from(Wallet)) or 0
        t24 = s.execute(select(func.count(), func.coalesce(func.sum(Trade.usd), 0)).where(Trade.ts >= now - 86400)).one()
        clusters, wallets = [], []
        if run:
            clusters = [_cluster_summary(c) for c in s.scalars(
                select(Cluster).where(Cluster.run_id == run.id).order_by(desc(Cluster.score)).limit(6))]
            ws = s.scalars(select(WalletScore).where(WalletScore.run_id == run.id)
                           .order_by(desc(WalletScore.score)).limit(8)).all()
            names = _names(s, [w.wallet for w in ws])
            wallets = [_wallet_summary(w, names.get(w.wallet, {}).get("name")) for w in ws]
        alerts = [_alert(a) for a in s.scalars(select(Alert).order_by(desc(Alert.created_at)).limit(8))]
        jumps = s.scalars(select(PriceJump).order_by(desc(PriceJump.start_ts)).limit(8)).all()
        mk = {m.condition_id: m for m in s.scalars(
            select(Market).where(Market.condition_id.in_([j.condition_id for j in jumps])))}
        cfg = _cfg(s)
        jl = []
        for j in jumps:
            buyers = _pre_jump_buyers(s, run, j, cfg, limit=50)
            jl.append({**_jump(j), "question": mk[j.condition_id].question if j.condition_id in mk else "",
                       "n_buyers": len(buyers),
                       "n_flagged": sum(1 for b in buyers if (b["score"] or 0) >= 50),
                       "usd_before": round(sum(b["usd"] for b in buyers), 2)})
        score_hist = []
        if run:
            vals = s.scalars(select(WalletScore.score).where(WalletScore.run_id == run.id)).all()
            if vals:
                h, _ = np.histogram(vals, bins=10, range=(0, 100))
                score_hist = [int(x) for x in h]
        return {
            "demo": env.demo_mode,
            "run": _run(run) if run else None,
            "totals": {"markets": tracked, "trades": n_trades, "wallets": n_wallets,
                       "trades_24h": int(t24[0]), "volume_24h": round(float(t24[1]), 2)},
            "top_clusters": clusters,
            "top_wallets": wallets,
            "alerts": alerts,
            "jumps": jl,
            "score_histogram": score_hist,
            "workers": worker_statuses(s),
        }


def _run(r: AnalysisRun) -> dict:
    st = r.stats or {}
    return {"id": r.id, "started_at": r.started_at, "finished_at": r.finished_at, "status": r.status,
            "duration_s": st.get("duration_s"), "wallets_scored": st.get("wallets_scored"),
            "clusters": st.get("clusters"), "edges": st.get("edges"), "jumps": st.get("jumps"),
            "trades": st.get("trades"), "error": r.error}


def _jump(j: PriceJump) -> dict:
    return {"id": j.id, "condition_id": j.condition_id, "start_ts": j.start_ts, "end_ts": j.end_ts,
            "p_before": j.p_before, "p_after": j.p_after, "direction": j.direction}


def _pre_jump_buyers(s: Session, run, j: PriceJump, cfg: dict, limit: int = 20) -> list[dict]:
    pre_s = int(cfg["analysis"]["pre_jump_hours"] * 3600)
    fav = or_(
        and_(Trade.side == "BUY", Trade.outcome_index == (0 if j.direction > 0 else 1)),
        and_(Trade.side == "SELL", Trade.outcome_index == (1 if j.direction > 0 else 0)),
    )
    rows = s.execute(
        select(Trade.wallet, func.sum(Trade.usd), func.min(Trade.ts), func.avg(Trade.price))
        .where(Trade.condition_id == j.condition_id, Trade.ts >= j.start_ts - pre_s, Trade.ts < j.start_ts, fav)
        .group_by(Trade.wallet).order_by(desc(func.sum(Trade.usd))).limit(limit)
    ).all()
    sc = _scores(s, run, [r[0] for r in rows])
    names = _names(s, [r[0] for r in rows])
    return [{"wallet": r[0], "usd": round(float(r[1]), 2), "first_ts": int(r[2]), "avg_price": round(float(r[3]), 4),
             "hours_before": round((j.start_ts - int(r[2])) / 3600, 1),
             "score": sc[r[0]].score if r[0] in sc else None,
             "cluster_key": sc[r[0]].cluster_key if r[0] in sc else None,
             "name": names.get(r[0], {}).get("name")} for r in rows]


# ---------------------------------------------------------------- clusters
@app.get("/api/clusters")
def clusters(min_score: float = 0, limit: int = Query(200, le=1000)):
    with session_scope() as s:
        run = latest_run(s)
        if not run:
            return []
        rows = s.scalars(select(Cluster).where(Cluster.run_id == run.id, Cluster.score >= min_score)
                         .order_by(desc(Cluster.score)).limit(limit)).all()
        return [_cluster_summary(c) for c in rows]


@app.get("/api/clusters/{key}")
def cluster_detail(key: str):
    with session_scope() as s:
        run = latest_run(s)
        c = s.get(Cluster, (run.id, key)) if run else None
        if c is None:
            # cluster may have dissolved: fall back to the latest run that had it
            c = s.scalars(select(Cluster).where(Cluster.key == key).order_by(desc(Cluster.run_id))).first()
            if c is None:
                raise HTTPException(404, "cluster not found")
        members = c.members
        sc = {w.wallet: w for w in s.scalars(
            select(WalletScore).where(WalletScore.run_id == c.run_id, WalletScore.wallet.in_(members)))}
        names = _names(s, members)
        edges = s.scalars(select(Edge).where(Edge.run_id == c.run_id, Edge.a.in_(members), Edge.b.in_(members))).all()
        trades = s.scalars(select(Trade).where(Trade.wallet.in_(members)).order_by(Trade.ts).limit(4000)).all()
        cids = sorted({t.condition_id for t in trades})
        mks = {m.condition_id: m for m in s.scalars(select(Market).where(Market.condition_id.in_(cids)))}
        jumps = defaultdict(list)
        for j in s.scalars(select(PriceJump).where(PriceJump.condition_id.in_(cids))):
            jumps[j.condition_id].append(_jump(j))
        transfers = s.scalars(select(Transfer).where(
            or_(Transfer.from_addr.in_(members), Transfer.to_addr.in_(members))).order_by(Transfer.ts).limit(2000)).all()
        kinds = {a.address: a for a in s.scalars(select(Address).where(Address.address.in_(
            list({t.from_addr for t in transfers} | {t.to_addr for t in transfers}))))}
        history = [{"run_id": r.run_id, "score": r.score, "size": r.size} for r in s.scalars(
            select(Cluster).where(Cluster.key == key).order_by(Cluster.run_id))]
        return {
            "cluster": {**_cluster_summary(c), "components": c.components, "stats": c.stats,
                        "top_markets": c.top_markets, "members": members, "run_id": c.run_id,
                        "stale": not run or c.run_id != run.id},
            "members": [{**(_wallet_summary(sc[m], names.get(m, {}).get("name")) if m in sc else
                            {"wallet": m, "name": names.get(m, {}).get("name"), "score": 0})} for m in members],
            "edges": [{"a": e.a, "b": e.b, "weight": e.weight, "kinds": e.kinds, "evidence": e.evidence} for e in edges],
            "trades": [_trade(t) for t in trades],
            "markets": {cid: {**_market_brief(m), "jumps": jumps.get(cid, [])} for cid, m in mks.items()},
            "transfers": [_transfer(t, kinds, set(members)) for t in transfers],
            "history": history,
        }


def _trade(t: Trade) -> dict:
    return {"seq": t.seq, "wallet": t.wallet, "condition_id": t.condition_id, "outcome": t.outcome,
            "outcome_index": t.outcome_index, "side": t.side, "price": t.price, "size": t.size,
            "usd": t.usd, "ts": t.ts, "tx_hash": t.tx_hash, "source": t.source}


def _transfer(t: Transfer, kinds: dict, members: set[str]) -> dict:
    def who(a: str) -> dict:
        k = kinds.get(a)
        return {"address": a, "kind": "member" if a in members else (k.kind if k else "external"),
                "label": k.label if k else None}

    return {"tx_hash": t.tx_hash, "from": who(t.from_addr), "to": who(t.to_addr), "amount": t.amount, "ts": t.ts}


# ------------------------------------------------------------------- graph
@app.get("/api/graph")
def graph(min_score: float = 0, max_nodes: int = Query(500, le=3000)):
    with session_scope() as s:
        run = latest_run(s)
        if not run:
            return {"nodes": [], "links": [], "clusters": []}
        cl = s.scalars(select(Cluster).where(Cluster.run_id == run.id, Cluster.score >= min_score)
                       .order_by(desc(Cluster.score))).all()
        keep: list[str] = []
        for c in cl:
            keep += c.members
            if len(keep) >= max_nodes:
                break
        keep_set = set(keep[:max_nodes])
        edges = s.scalars(select(Edge).where(Edge.run_id == run.id)).all()
        edges = [e for e in edges if e.a in keep_set and e.b in keep_set]
        sc = _scores(s, run, keep_set)
        names = _names(s, keep_set)
        return {
            "nodes": [{"id": w, "score": sc[w].score if w in sc else 0, "cluster": sc[w].cluster_key if w in sc else None,
                       "volume": (sc[w].stats or {}).get("volume", 0) if w in sc else 0,
                       "name": names.get(w, {}).get("name")} for w in keep_set],
            "links": [{"source": e.a, "target": e.b, "weight": e.weight, "kinds": e.kinds} for e in edges],
            "clusters": [{"key": c.key, "score": c.score, "size": c.size} for c in cl],
        }


# ----------------------------------------------------------------- wallets
@app.get("/api/wallets")
def wallets(sort: Literal["score", "volume", "pnl"] = "score", q: str = "", limit: int = Query(100, le=1000)):
    with session_scope() as s:
        run = latest_run(s)
        if not run:
            return []
        stmt = select(WalletScore).where(WalletScore.run_id == run.id)
        if q:
            ql = q.lower()
            name_hits = s.scalars(select(Wallet.address).where(
                or_(func.lower(Wallet.name).contains(ql), func.lower(Wallet.pseudonym).contains(ql)))).all()
            stmt = stmt.where(or_(WalletScore.wallet.contains(ql), WalletScore.wallet.in_(name_hits)))
        if sort == "score":
            stmt = stmt.order_by(desc(WalletScore.score)).limit(limit)
            rows = s.scalars(stmt).all()
        else:
            rows = s.scalars(stmt).all()
            rows = sorted(rows, key=lambda w: -(w.stats or {}).get(sort, 0) or 0)[:limit]
        names = _names(s, [w.wallet for w in rows])
        return [_wallet_summary(w, names.get(w.wallet, {}).get("name")) for w in rows]


@app.get("/api/wallets/{address}")
def wallet_detail(address: str):
    address = address.lower()
    with session_scope() as s:
        w = s.get(Wallet, address)
        if w is None:
            raise HTTPException(404, "wallet not seen in tracked markets")
        run = latest_run(s)
        ws = s.get(WalletScore, (run.id, address)) if run else None
        cluster = s.get(Cluster, (run.id, ws.cluster_key)) if (run and ws and ws.cluster_key) else None
        history = [{"run_id": r.run_id, "score": r.score} for r in s.scalars(
            select(WalletScore).where(WalletScore.wallet == address).order_by(WalletScore.run_id))]
        trades = s.scalars(select(Trade).where(Trade.wallet == address).order_by(desc(Trade.ts)).limit(500)).all()
        cids = {t.condition_id for t in trades}
        mks = {m.condition_id: m for m in s.scalars(select(Market).where(Market.condition_id.in_(list(cids))))}
        # positions per market
        pos: dict[str, dict] = {}
        for t in trades:
            p = pos.setdefault(t.condition_id, {"condition_id": t.condition_id, "buy_usd": 0.0, "sell_usd": 0.0,
                                                "shares": defaultdict(float), "first_ts": t.ts, "last_ts": t.ts})
            sign = 1 if t.side == "BUY" else -1
            p["shares"][t.outcome_index] += sign * t.size
            p["buy_usd" if t.side == "BUY" else "sell_usd"] += t.usd
            p["first_ts"], p["last_ts"] = min(p["first_ts"], t.ts), max(p["last_ts"], t.ts)
        positions = []
        for cid, p in pos.items():
            m = mks.get(cid)
            shares = {k: round(v, 2) for k, v in p["shares"].items()}
            main = max(shares, key=lambda k: shares[k]) if shares else 0
            payout = max(shares.get(m.winning_index, 0), 0) if (m and m.resolved and m.winning_index is not None) else None
            positions.append({
                "market": _market_brief(m) or {"condition_id": cid, "question": cid[:12]},
                "buy_usd": round(p["buy_usd"], 2), "sell_usd": round(p["sell_usd"], 2),
                "main_outcome": main, "shares": shares,
                "pnl": round(p["sell_usd"] + payout - p["buy_usd"], 2) if payout is not None else None,
                "won": (main == m.winning_index) if (m and m.resolved) else None,
                "first_ts": p["first_ts"], "last_ts": p["last_ts"],
            })
        positions.sort(key=lambda x: -x["buy_usd"])
        transfers = s.scalars(select(Transfer).where(or_(Transfer.from_addr == address, Transfer.to_addr == address))
                              .order_by(Transfer.ts).limit(500)).all()
        others = {t.from_addr for t in transfers} | {t.to_addr for t in transfers}
        kinds = {a.address: a for a in s.scalars(select(Address).where(Address.address.in_(list(others))))}
        tracked_others = set(s.scalars(select(Wallet.address).where(Wallet.address.in_(list(others)))).all())
        edges = []
        if run:
            edges = s.scalars(select(Edge).where(Edge.run_id == run.id, or_(Edge.a == address, Edge.b == address))).all()
        neighbor_scores = _scores(s, run, [e.b if e.a == address else e.a for e in edges])
        names = _names(s, list(neighbor_scores))

        def who(a: str) -> dict:
            k = kinds.get(a)
            kind = "tracked" if a in tracked_others else (k.kind if k else "external")
            return {"address": a, "kind": kind, "label": k.label if k else None}

        return {
            "wallet": {"address": w.address, "name": w.name, "pseudonym": w.pseudonym,
                       "first_trade_ts": w.first_trade_ts, "last_trade_ts": w.last_trade_ts,
                       "first_funded_ts": w.first_funded_ts, "traced_at": w.traced_at, "trace_status": w.trace_status},
            "score": {"score": ws.score, "components": ws.components, "stats": ws.stats,
                      "cluster_key": ws.cluster_key} if ws else None,
            "cluster": _cluster_summary(cluster) if cluster else None,
            "history": history,
            "positions": positions,
            "trades": [{**_trade(t), "question": mks[t.condition_id].question if t.condition_id in mks else None}
                       for t in trades],
            "transfers": [{"tx_hash": t.tx_hash, "direction": "in" if t.to_addr == address else "out",
                           "counterparty": who(t.from_addr if t.to_addr == address else t.to_addr),
                           "amount": t.amount, "ts": t.ts} for t in transfers],
            "links": [{"wallet": (e.b if e.a == address else e.a), "weight": e.weight, "kinds": e.kinds,
                       "evidence": e.evidence,
                       "score": neighbor_scores[(e.b if e.a == address else e.a)].score
                       if (e.b if e.a == address else e.a) in neighbor_scores else None,
                       "name": names.get(e.b if e.a == address else e.a, {}).get("name")} for e in edges],
        }


# ----------------------------------------------------------------- markets
@app.get("/api/markets")
def markets(status: Literal["all", "open", "resolved"] = "all", q: str = "", tracked_only: bool = True):
    with session_scope() as s:
        run = latest_run(s)
        stmt = select(Market)
        if tracked_only:
            stmt = stmt.where(Market.tracked)
        if status == "open":
            stmt = stmt.where(Market.closed.is_(False))
        elif status == "resolved":
            stmt = stmt.where(Market.closed.is_(True))
        if q:
            stmt = stmt.where(func.lower(Market.question).contains(q.lower()))
        ms = s.scalars(stmt.order_by(desc(Market.volume)).limit(500)).all()
        cids = [m.condition_id for m in ms]
        agg = {r[0]: r[1:] for r in s.execute(
            select(Trade.condition_id, func.count(), func.count(func.distinct(Trade.wallet)), func.sum(Trade.usd),
                   func.max(Trade.ts)).where(Trade.condition_id.in_(cids)).group_by(Trade.condition_id))}
        n_jumps = dict(s.execute(select(PriceJump.condition_id, func.count())
                                 .where(PriceJump.condition_id.in_(cids)).group_by(PriceJump.condition_id)).all())
        flagged: dict[str, int] = {}
        if run:
            hot = select(WalletScore.wallet).where(WalletScore.run_id == run.id, WalletScore.score >= 50).subquery()
            flagged = dict(s.execute(
                select(Trade.condition_id, func.count(func.distinct(Trade.wallet)))
                .where(Trade.condition_id.in_(cids), Trade.wallet.in_(select(hot.c.wallet)))
                .group_by(Trade.condition_id)).all())
        out = []
        for m in ms:
            a = agg.get(m.condition_id, (0, 0, 0, None))
            out.append({**_market_brief(m), "tags": m.tags, "volume": m.volume, "closed": m.closed,
                        "end_ts": m.end_ts, "closed_ts": m.closed_ts, "event_title": m.event_title,
                        "n_trades": int(a[0] or 0), "n_wallets": int(a[1] or 0), "our_volume": round(float(a[2] or 0), 2),
                        "last_trade_ts": a[3], "n_jumps": int(n_jumps.get(m.condition_id, 0)),
                        "n_flagged": int(flagged.get(m.condition_id, 0)),
                        "backfilled": m.backfilled_at is not None})
        return out


@app.get("/api/markets/{condition_id}")
def market_detail(condition_id: str, min_flag: float = 30):
    cid = condition_id.lower()
    with session_scope() as s:
        m = s.get(Market, cid)
        if m is None:
            m = s.scalars(select(Market).where(Market.slug == condition_id)).first()
            if m is None:
                raise HTTPException(404, "market not found")
            cid = m.condition_id
        run = latest_run(s)
        cfg = _cfg(s)
        token0 = (m.token_ids or [None])[0]
        series: list[tuple[int, float]] = []
        if token0:
            series = [(r[0], r[1]) for r in s.execute(
                select(PricePoint.ts, PricePoint.price).where(PricePoint.token_id == token0).order_by(PricePoint.ts))]
        if len(series) < 10:
            rows = s.execute(select(Trade.ts, Trade.price, Trade.outcome_index).where(Trade.condition_id == cid)
                             .order_by(Trade.ts)).all()
            series = [(r[0], r[1] if r[2] == 0 else 1 - r[1]) for r in rows]
        if len(series) > 1500:
            step = len(series) // 1500 + 1
            series = series[::step] + [series[-1]]
        jumps = s.scalars(select(PriceJump).where(PriceJump.condition_id == cid).order_by(PriceJump.start_ts)).all()
        jump_list = [{**_jump(j), "buyers": _pre_jump_buyers(s, run, j, cfg)} for j in jumps]
        # trades from flagged wallets + all large trades
        hot = []
        if run:
            hot = s.scalars(select(WalletScore.wallet).where(WalletScore.run_id == run.id,
                                                             WalletScore.score >= min_flag)).all()
        trades = s.scalars(select(Trade).where(Trade.condition_id == cid, or_(Trade.wallet.in_(hot), Trade.usd >= 5000))
                           .order_by(Trade.ts).limit(2000)).all()
        sc = _scores(s, run, [t.wallet for t in trades])
        names = _names(s, [t.wallet for t in trades])
        holders = s.execute(
            select(Trade.wallet, Trade.outcome_index,
                   func.sum(Trade.usd).label("usd"))
            .where(Trade.condition_id == cid, Trade.side == "BUY")
            .group_by(Trade.wallet, Trade.outcome_index).order_by(desc("usd")).limit(25)).all()
        hsc = _scores(s, run, [h[0] for h in holders])
        hnames = _names(s, [h[0] for h in holders])
        totals = s.execute(select(func.count(), func.count(func.distinct(Trade.wallet)), func.coalesce(func.sum(Trade.usd), 0))
                           .where(Trade.condition_id == cid)).one()
        return {
            "market": {**_market_brief(m), "tags": m.tags, "volume": m.volume, "event_title": m.event_title,
                       "end_ts": m.end_ts, "closed": m.closed, "closed_ts": m.closed_ts, "token_ids": m.token_ids,
                       "n_trades": totals[0], "n_wallets": totals[1], "our_volume": round(float(totals[2]), 2)},
            "series": [{"ts": t, "p": round(p, 4)} for t, p in series],
            "jumps": jump_list,
            "flagged_trades": [{**_trade(t), "score": sc[t.wallet].score if t.wallet in sc else None,
                                "cluster_key": sc[t.wallet].cluster_key if t.wallet in sc else None,
                                "name": names.get(t.wallet, {}).get("name")} for t in trades],
            "top_buyers": [{"wallet": h[0], "outcome_index": h[1], "usd": round(float(h[2]), 2),
                            "score": hsc[h[0]].score if h[0] in hsc else None,
                            "cluster_key": hsc[h[0]].cluster_key if h[0] in hsc else None,
                            "name": hnames.get(h[0], {}).get("name")} for h in holders],
        }


# -------------------------------------------------------------------- feed
@app.get("/api/feed")
def feed(limit: int = Query(60, le=500), min_usd: float = 0, flagged_only: bool = False, min_score: float = 30):
    with session_scope() as s:
        return _feed(s, limit=limit, min_usd=min_usd, flagged_only=flagged_only, min_score=min_score)


def _feed(s: Session, *, limit: int, min_usd: float = 0, flagged_only: bool = False, min_score: float = 30,
          after_seq: int | None = None) -> list[dict]:
    run = latest_run(s)
    stmt = select(Trade).where(Trade.usd >= min_usd, Trade.source != "backfill")
    if after_seq is not None:
        stmt = stmt.where(Trade.seq > after_seq)
    if flagged_only and run:
        hot = select(WalletScore.wallet).where(WalletScore.run_id == run.id, WalletScore.score >= min_score)
        stmt = stmt.where(Trade.wallet.in_(hot))
    order = (desc(Trade.seq),) if after_seq is not None else (desc(Trade.ts), desc(Trade.seq))
    trades = s.scalars(stmt.order_by(*order).limit(limit)).all()
    sc = _scores(s, run, [t.wallet for t in trades])
    names = _names(s, [t.wallet for t in trades])
    mks = {m.condition_id: m for m in s.scalars(select(Market).where(
        Market.condition_id.in_(list({t.condition_id for t in trades}))))}
    return [{**_trade(t), "score": sc[t.wallet].score if t.wallet in sc else None,
             "cluster_key": sc[t.wallet].cluster_key if t.wallet in sc else None,
             "name": names.get(t.wallet, {}).get("name"),
             "question": mks[t.condition_id].question if t.condition_id in mks else None} for t in trades]


@app.get("/api/stream")
async def stream():
    """Server-sent events: new trades, alerts and analysis runs."""

    def snapshot():
        with session_scope() as s:
            return (s.scalar(select(func.max(Trade.seq))) or 0,
                    s.scalar(select(func.max(Alert.id))) or 0,
                    (latest_run(s).id if latest_run(s) else 0))

    def poll(last_seq, last_alert, last_run):
        with session_scope() as s:
            trades = _feed(s, limit=50, after_seq=last_seq)
            alerts = [_alert(a) for a in s.scalars(select(Alert).where(Alert.id > last_alert).order_by(Alert.id))]
            run = latest_run(s)
            return trades, alerts, (_run(run) if run and run.id > last_run else None)

    async def gen():
        last_seq, last_alert, last_run = await asyncio.to_thread(snapshot)
        yield "retry: 3000\n\n"
        while True:
            try:
                trades, alerts, run = await asyncio.to_thread(poll, last_seq, last_alert, last_run)
            except Exception as exc:  # database hiccup: tell the client, keep going
                yield f"event: error\ndata: {json.dumps(str(exc)[:200])}\n\n"
                await asyncio.sleep(5)
                continue
            for t in reversed(trades):
                last_seq = max(last_seq, t["seq"])
                yield f"event: trade\ndata: {json.dumps(t)}\n\n"
            for a in alerts:
                last_alert = max(last_alert, a["id"])
                yield f"event: alert\ndata: {json.dumps(a)}\n\n"
            if run:
                last_run = run["id"]
                yield f"event: run\ndata: {json.dumps(run)}\n\n"
            yield ": keep-alive\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ------------------------------------------------------------------ alerts
@app.get("/api/alerts")
def list_alerts(limit: int = Query(100, le=500), unread_only: bool = False):
    with session_scope() as s:
        stmt = select(Alert)
        if unread_only:
            stmt = stmt.where(Alert.acknowledged.is_(False))
        return [_alert(a) for a in s.scalars(stmt.order_by(desc(Alert.created_at)).limit(limit))]


@app.post("/api/alerts/{alert_id}/ack")
def ack_alert(alert_id: int):
    with session_scope() as s:
        a = s.get(Alert, alert_id)
        if not a:
            raise HTTPException(404)
        a.acknowledged = True
    return {"ok": True}


@app.post("/api/alerts/ack-all")
def ack_all():
    with session_scope() as s:
        for a in s.scalars(select(Alert).where(Alert.acknowledged.is_(False))):
            a.acknowledged = True
    return {"ok": True}


@app.post("/api/alerts/test")
async def test_alert():
    from ..workers.alerts import deliver

    if not ((env.telegram_bot_token and env.telegram_chat_id) or env.discord_webhook_url):
        raise HTTPException(400, "No Telegram or Discord channel configured in .env")
    await deliver([{"id": None, "kind": "cluster", "ref": "test", "score": 0,
                    "title": "PolyTracker test notification", "message": "Alerts are wired up correctly."}])
    return {"ok": True}


# ---------------------------------------------------------------- settings
class ScopeIn(BaseModel):
    mode: Literal["tags", "all", "explicit"] = "tags"
    tags: list[str] = Field(default_factory=list)
    include_keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    explicit_markets: list[str] = Field(default_factory=list)
    min_volume: float = Field(25000, ge=0)
    max_markets: int = Field(150, ge=1, le=2000)
    include_closed_days: int = Field(90, ge=0, le=1095)


class AlertsIn(BaseModel):
    enabled: bool = True
    cluster_score: float = Field(70, ge=0, le=100)
    wallet_score: float = Field(80, ge=0, le=100)
    large_trade_usd: float = Field(25000, ge=0)
    cooldown_hours: float = Field(12, ge=0)


class SettingsIn(BaseModel):
    scope: ScopeIn | None = None
    alerts: AlertsIn | None = None


@app.get("/api/settings")
def get_settings():
    with session_scope() as s:
        overrides = config_overrides(s)
        cfg = effective_config(overrides)
        oc = cfg["onchain"]
        return {
            "demo": env.demo_mode,
            "scope": cfg["scope"],
            "alerts": cfg["alerts"],
            "analysis": cfg["analysis"],
            "ingestion": cfg["ingestion"],
            "onchain": {k: oc[k] for k in ("provider", "chain_id", "trace_depth", "min_wallet_volume_usd",
                                           "max_traces_per_cycle")},
            "overridden": sorted(overrides.keys()),
            "integrations": {
                "onchain": bool(env.etherscan_api_key if oc["provider"] == "etherscan" else env.blockscout_api_key),
                "telegram": bool(env.telegram_bot_token and env.telegram_chat_id),
                "discord": bool(env.discord_webhook_url),
            },
        }


@app.put("/api/settings")
def put_settings(body: SettingsIn):
    with session_scope() as s:
        overrides = config_overrides(s)
        if body.scope is not None:
            overrides["scope"] = body.scope.model_dump()
            set_setting(s, "market_sync_requested_at", int(time.time()))
        if body.alerts is not None:
            overrides["alerts"] = body.alerts.model_dump()
        overrides = {k: v for k, v in overrides.items() if k in EDITABLE_SECTIONS}
        set_setting(s, "config_overrides", overrides)
    return get_settings()


@app.delete("/api/settings")
def reset_settings():
    with session_scope() as s:
        set_setting(s, "config_overrides", {})
        set_setting(s, "market_sync_requested_at", int(time.time()))
    return get_settings()


# ------------------------------------------------------------------ status
@app.post("/api/analysis/run")
def request_analysis():
    with session_scope() as s:
        set_setting(s, "analysis_requested_at", int(time.time()))
    return {"ok": True, "requested_at": int(time.time())}


@app.get("/api/status")
def status():
    with session_scope() as s:
        runs = s.scalars(select(AnalysisRun).order_by(desc(AnalysisRun.id)).limit(10)).all()
        counts = {
            "markets": s.scalar(select(func.count()).select_from(Market)) or 0,
            "tracked_markets": s.scalar(select(func.count()).select_from(Market).where(Market.tracked)) or 0,
            "backfilled_markets": s.scalar(select(func.count()).select_from(Market).where(
                Market.tracked, Market.backfilled_at.is_not(None))) or 0,
            "trades": s.scalar(select(func.count()).select_from(Trade)) or 0,
            "wallets": s.scalar(select(func.count()).select_from(Wallet)) or 0,
            "traced_wallets": s.scalar(select(func.count()).select_from(Wallet).where(Wallet.traced_at.is_not(None))) or 0,
            "transfers": s.scalar(select(func.count()).select_from(Transfer)) or 0,
            "price_points": s.scalar(select(func.count()).select_from(PricePoint)) or 0,
            "hubs": s.scalar(select(func.count()).select_from(Address).where(Address.kind == "hub")) or 0,
        }
        return {
            "demo": env.demo_mode,
            "time": int(time.time()),
            "workers": worker_statuses(s),
            "runs": [_run(r) for r in runs],
            "counts": counts,
            "analysis_requested_at": get_setting(s, "analysis_requested_at", 0),
        }


# ---------------------------------------------------- optional static UI
_static = os.environ.get("STATIC_DIR")
if _static and Path(_static).is_dir():
    app.mount("/assets", StaticFiles(directory=Path(_static) / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = Path(_static) / path
        if path and f.is_file():
            return FileResponse(f)
        return FileResponse(Path(_static) / "index.html")
