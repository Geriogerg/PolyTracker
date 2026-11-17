"""Link wallets that act together, keeping the evidence for every link.

Link kinds (weights from config):
  direct_transfer     money moved straight between two tracked wallets
  funding_shared      both funded by the same non-hub address (1 or 2 hops up)
  withdrawal_shared   both cashed out to the same non-hub address
  co_bet              same market, same direction, within minutes, in a small
                      crowd, on several distinct markets
  behavior            similar fingerprint; only reinforces an existing link

Exchanges, bridges and protocol contracts ("hubs") fund thousands of
unrelated users, so any address with a large fan-out is excluded from
funding/withdrawal links.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations

import networkx as nx
import numpy as np
import pandas as pd

from .features import behaviour_vectors, cosine


@dataclass
class EdgeAcc:
    weight: float = 0.0
    kinds: set = field(default_factory=set)
    evidence: list = field(default_factory=list)


class EdgeBook:
    def __init__(self):
        self.edges: dict[tuple[str, str], EdgeAcc] = {}

    def add(self, a: str, b: str, kind: str, weight: float, evidence: dict) -> None:
        if a == b:
            return
        key = (a, b) if a < b else (b, a)
        acc = self.edges.setdefault(key, EdgeAcc())
        acc.weight += weight
        acc.kinds.add(kind)
        if len(acc.evidence) < 12:
            acc.evidence.append({"kind": kind, **evidence})

    def __len__(self) -> int:
        return len(self.edges)


# ------------------------------------------------------------------- hubs
def detect_hubs(
    transfers: pd.DataFrame,
    tracked: set[str],
    static_hubs: set[str],
    fanout_min: int,
) -> set[str]:
    hubs = set(static_hubs)
    if transfers.empty:
        return hubs - tracked
    to_tracked = transfers[transfers["to_addr"].isin(tracked)]
    from_tracked = transfers[transfers["from_addr"].isin(tracked)]
    fan_out = to_tracked.groupby("from_addr")["to_addr"].nunique()
    fan_in = from_tracked.groupby("to_addr")["from_addr"].nunique()
    hubs |= set(fan_out[fan_out >= fanout_min].index)
    hubs |= set(fan_in[fan_in >= fanout_min].index)
    return hubs - tracked


# --------------------------------------------------------- on-chain links
def onchain_edges(
    book: EdgeBook,
    transfers: pd.DataFrame,
    tracked: set[str],
    hubs: set[str],
    weights: dict,
    depth: int = 2,
    min_amount: float = 1.0,
) -> dict[str, dict[str, int]]:
    """Adds transfer/funding/withdrawal edges. Returns wallet -> {root: hops}."""
    roots: dict[str, dict[str, int]] = {w: {} for w in tracked}
    if transfers.empty:
        return roots
    t = transfers[transfers["amount"] >= min_amount]

    # direct transfers between tracked wallets
    direct = t[t["from_addr"].isin(tracked) & t["to_addr"].isin(tracked)]
    for r in direct.itertuples(index=False):
        book.add(
            r.from_addr, r.to_addr, "direct_transfer", weights["direct_transfer"],
            {"from": r.from_addr, "to": r.to_addr, "amount": round(r.amount, 2), "ts": int(r.ts), "tx": r.tx_hash},
        )

    # funding roots, walking up to `depth` hops through non-hub senders
    inc = t[~t["from_addr"].isin(hubs)]
    senders: dict[str, set[str]] = inc.groupby("to_addr")["from_addr"].agg(set).to_dict()
    first_in = inc.groupby(["to_addr", "from_addr"])["ts"].min().to_dict()
    for w in tracked:
        r: dict[str, int] = {}
        frontier = {w}
        for hop in range(1, depth + 1):
            nxt = set()
            for a in frontier:
                for s in senders.get(a, ()):
                    if s == w or s in r:
                        continue
                    r[s] = hop
                    nxt.add(s)
            frontier = nxt
        roots[w] = r

    by_root: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for w, r in roots.items():
        for root, hop in r.items():
            by_root[root].append((w, hop))
    for root, members in by_root.items():
        if len(members) < 2 or len(members) > 60:
            continue
        for (a, ha), (b, hb) in combinations(members, 2):
            two_hop = max(ha, hb) > 1
            w_ = weights["funding_shared_2hop"] if two_hop else weights["funding_shared"]
            ev = {"via": root, "hops": [ha, hb]}
            ta, tb = first_in.get((a, root)), first_in.get((b, root))
            if ta and tb:
                ev["funded_gap_h"] = round(abs(ta - tb) / 3600, 1)
            book.add(a, b, "funding_shared", w_, ev)

    # shared cash-out destinations
    out = t[t["from_addr"].isin(tracked) & ~t["to_addr"].isin(hubs) & ~t["to_addr"].isin(tracked)]
    dests = out.groupby("to_addr")["from_addr"].agg(set)
    for dest, ws in dests.items():
        if 2 <= len(ws) <= 60:
            for a, b in combinations(sorted(ws), 2):
                book.add(a, b, "withdrawal_shared", weights["withdrawal_shared"], {"via": dest})
    return roots


# ------------------------------------------------------------- co-betting
def co_bet_edges(
    book: EdgeBook,
    bets: pd.DataFrame,
    window_s: int,
    max_crowd: int,
    min_markets: int,
    weight: float,
) -> None:
    if bets.empty:
        return
    pair_markets: dict[tuple[str, str], dict[str, dict]] = defaultdict(dict)
    for (m, d), grp in bets.groupby(["condition_id", "dir"], sort=False):
        if grp["wallet"].nunique() < 2:
            continue
        g = grp.sort_values("ts")
        ts = g["ts"].to_numpy()
        ws = g["wallet"].to_numpy()
        n = len(g)
        for i in range(n):
            j_end = int(np.searchsorted(ts, ts[i] + window_s, side="right"))
            if j_end - i < 2:
                continue
            crowd = set(ws[i:j_end])
            if len(crowd) < 2 or len(crowd) > max_crowd:
                continue
            for k in range(i + 1, j_end):
                if ws[k] == ws[i]:
                    continue
                a, b = (ws[i], ws[k]) if ws[i] < ws[k] else (ws[k], ws[i])
                prev = pair_markets[(a, b)].get(m)
                if prev is None or len(crowd) < prev["crowd"]:
                    pair_markets[(a, b)][m] = {
                        "condition_id": m, "dir": int(d), "ts": int(ts[i]),
                        "gap_min": round((ts[k] - ts[i]) / 60, 1), "crowd": len(crowd),
                    }
    for (a, b), markets in pair_markets.items():
        if len(markets) < min_markets:
            continue
        w = sum(weight * 3.0 / math.log2(2 + mk["crowd"]) for mk in markets.values())
        ev = sorted(markets.values(), key=lambda x: x["ts"])[:8]
        book.add(a, b, "co_bet", w, {"n_markets": len(markets), "markets": ev})


def behaviour_reinforcement(book: EdgeBook, bets: pd.DataFrame, weight: float, min_sim: float = 0.9) -> None:
    if not len(book) or bets.empty:
        return
    vecs = behaviour_vectors(bets)
    if vecs.empty:
        return
    idx = {w: i for i, w in enumerate(vecs.index)}
    mat = vecs.to_numpy()
    for (a, b), acc in list(book.edges.items()):
        if a in idx and b in idx:
            sim = cosine(mat[idx[a]], mat[idx[b]])
            if sim >= min_sim:
                acc.weight += weight * sim
                acc.kinds.add("behavior")
                acc.evidence.append({"kind": "behavior", "similarity": round(sim, 3)})


# ------------------------------------------------------------ communities
def communities(book: EdgeBook, min_weight: float, seed: int = 42) -> tuple[nx.Graph, list[set[str]]]:
    G = nx.Graph()
    for (a, b), acc in book.edges.items():
        structural = acc.kinds & {"direct_transfer", "funding_shared", "withdrawal_shared"}
        if acc.weight >= min_weight or structural:
            G.add_edge(a, b, weight=acc.weight)
    if G.number_of_edges() == 0:
        return G, []
    comms = nx.community.louvain_communities(G, weight="weight", seed=seed, resolution=1.0)
    return G, [c for c in comms if len(c) >= 2]
