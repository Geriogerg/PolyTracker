"""Explainable suspicion scores (0-100) for wallets and clusters.

Every score is a sum of components; each component carries its points, its
maximum and a one-line plain-language reason, so the dashboard can show
exactly why something was flagged.
"""
from __future__ import annotations

from collections import Counter

import numpy as np
import pandas as pd

from .features import JumpIndex, timing_trials
from .stats import fmt_p, points_from_p, poisson_binomial_sf

STRUCTURAL = {"direct_transfer", "funding_shared", "withdrawal_shared"}


def _component(key: str, label: str, points: float, max_points: float, detail: str) -> dict:
    return {"key": key, "label": label, "points": round(float(points), 1), "max": max_points, "detail": detail}


def _cents(p: float) -> str:
    return f"{p * 100:.0f}¢"


def _usd(x: float) -> str:
    x = float(x)
    if abs(x) >= 1e6:
        return f"${x / 1e6:.2f}M"
    if abs(x) >= 1e3:
        return f"${x / 1e3:.1f}k"
    return f"${x:.0f}"


# ------------------------------------------------------------------ wallet
def score_wallet(
    wallet: str,
    bets: pd.DataFrame,
    positions: pd.DataFrame,
    jx: JumpIndex,
    cfg: dict,
    first_funded: int | None,
    first_trade: int | None,
    n_trades: int,
    structural_links: int,
) -> tuple[float, list[dict], dict]:
    a = cfg["analysis"]
    pre_s = int(a["pre_jump_hours"] * 3600)
    fresh_s = int(a["fresh_wallet_hours"] * 3600)
    comps: list[dict] = []

    # 1. improbable wins ------------------------------------------------
    n_pos = len(positions)
    wins = int(positions["won"].sum()) if n_pos else 0
    exp_wins = float(positions["entry_p"].sum()) if n_pos else 0.0
    p_wins = 1.0
    if n_pos >= 2 and wins > exp_wins:
        p_wins = poisson_binomial_sf(positions["entry_p"].tolist(), wins)
    pts = points_from_p(p_wins, 35) if n_pos >= 2 else 0.0
    if n_pos:
        won_entries = positions.loc[positions["won"], "entry_p"]
        avg = _cents(float(won_entries.mean())) if len(won_entries) else "n/a"
        detail = (
            f"{wins}/{n_pos} resolved bets won, avg winning entry {avg}; "
            f"no-edge expectation {exp_wins:.1f} wins (p = {fmt_p(p_wins)})"
        )
    else:
        detail = "No resolved positions yet"
    comps.append(_component("improbable_wins", "Wins against the odds", pts, 35, detail))

    # 2. pre-move timing ------------------------------------------------
    trials = timing_trials(bets, jx, pre_s)
    hits = [t for t in trials if t.hit]
    p_time = poisson_binomial_sf([t.q for t in trials], len(hits)) if hits else 1.0
    pts = points_from_p(p_time, 30) if hits else 0.0
    hit_usd = sum(t.hit_usd for t in hits)
    if trials:
        detail = (
            f"{len(hits)} of {len(trials)} betting episodes came in the {a['pre_jump_hours']} h before a "
            f"≥{a['jump_threshold'] * 100:.0f}-pt move their way ({_usd(hit_usd)}); "
            f"chance ≈ {sum(t.q for t in trials):.2f} (p = {fmt_p(p_time)})"
        )
    else:
        detail = "No bets in markets with detected price jumps"
    comps.append(_component("pre_move_timing", "Bought before the news", pts, 30, detail))

    # 3. fresh wallet ---------------------------------------------------
    max_fresh, fresh_age_h = 0.0, None
    if first_funded:
        buys = bets[(bets["side"] == "BUY") & (bets["usd"] >= 1000)]
        early = buys[buys["ts"] - first_funded <= fresh_s]
        if len(early):
            max_fresh = float(early["usd"].max())
            fresh_age_h = float((early.loc[early["usd"].idxmax(), "ts"] - first_funded) / 3600)
        pts = 15 * min(1.0, max_fresh / 20000)
        detail = (
            f"Bet {_usd(max_fresh)} {max(fresh_age_h, 0):.0f} h after the wallet was first funded"
            if max_fresh
            else "No large bet in the wallet's first days"
        )
    else:
        pts, detail = 0.0, "On-chain age not traced yet"
    comps.append(_component("fresh_wallet", "New wallet, big bet", pts, 15, detail))

    # 4. sized conviction ----------------------------------------------
    paid = 0.0
    if n_pos:
        lw = positions[(positions["won"]) & (positions["entry_p"] <= 0.3)]
        paid += float(lw["stake"].sum())
    paid += sum(t.hit_usd for t in hits)
    pts = 10 * min(1.0, paid / 50000)
    detail = f"{_usd(paid)} staked on longshots that paid off or on moves that came" if paid else "—"
    comps.append(_component("sized_conviction", "Size of the right calls", pts, 10, detail))

    # 5. network --------------------------------------------------------
    indiv = sum(c["points"] for c in comps if c["key"] in ("improbable_wins", "pre_move_timing", "sized_conviction"))
    pts = 10 * min(1.0, structural_links / 3) * (1.0 if indiv > 0 else 0.3)
    detail = f"On-chain links to {structural_links} other tracked wallet(s)" if structural_links else "No on-chain links"
    comps.append(_component("network", "On-chain ties", pts, 10, detail))

    score = round(min(100.0, sum(c["points"] for c in comps)), 1)
    vol = float(bets["usd"].sum()) if len(bets) else 0.0
    pnl = float(positions["pnl"].sum()) if n_pos else 0.0
    cost = float(positions["cost"].sum()) if n_pos else 0.0
    stats = {
        "n_trades": int(n_trades),
        "n_bets": int(len(bets)),
        "volume": round(vol, 2),
        "n_markets": int(bets["condition_id"].nunique()) if len(bets) else 0,
        "resolved": n_pos,
        "wins": wins,
        "expected_wins": round(exp_wins, 2),
        "win_rate": round(wins / n_pos, 4) if n_pos else None,
        "p_wins": p_wins,
        "pnl": round(pnl, 2),
        "roi": round(pnl / cost, 4) if cost else None,
        "timing_trials": len(trials),
        "timing_hits": len(hits),
        "p_timing": p_time,
        "pre_jump_usd": round(hit_usd, 2),
        "first_funded_ts": first_funded,
        "first_trade_ts": first_trade,
        "max_fresh_bet": round(max_fresh, 2),
        "last_ts": int(bets["ts_last"].max()) if len(bets) else None,
        "structural_links": structural_links,
    }
    return score, comps, stats


# ----------------------------------------------------------------- cluster
def pooled_positions(positions: pd.DataFrame) -> pd.DataFrame:
    """Treat the cluster as one trader: one position per market (members are
    not independent, so pooling them per market avoids counting a single
    tip nine times)."""
    if positions.empty:
        return positions
    p = positions.copy()
    p["shares"] = p["stake"] / p["entry_p"]
    g = p.groupby(["condition_id", "primary_outcome"]).agg(
        stake=("stake", "sum"), shares=("shares", "sum"), won=("won", "first"), members=("wallet", "nunique")
    ).reset_index()
    g = g.loc[g.groupby("condition_id")["stake"].idxmax()]
    g["entry_p"] = (g["stake"] / g["shares"]).clip(0.01, 0.99)
    return g


def score_cluster(
    members: list[str],
    bets: pd.DataFrame,
    positions: pd.DataFrame,
    jx: JumpIndex,
    cfg: dict,
    edges: list[tuple[str, str, dict]],
    member_scores: dict[str, float],
    member_stats: dict[str, dict],
    markets: dict[str, dict],
) -> tuple[float, list[dict], dict, list[dict]]:
    a = cfg["analysis"]
    pre_s = int(a["pre_jump_hours"] * 3600)
    size = len(members)
    comps: list[dict] = []

    # 1. pooled wins ----------------------------------------------------
    pooled = pooled_positions(positions)
    n, k = len(pooled), int(pooled["won"].sum()) if len(pooled) else 0
    exp = float(pooled["entry_p"].sum()) if n else 0.0
    p_w = poisson_binomial_sf(pooled["entry_p"].tolist(), k) if n >= 2 and k > exp else 1.0
    pts = points_from_p(p_w, 35) if n >= 2 else 0.0
    if n:
        we = pooled.loc[pooled["won"], "entry_p"]
        avg = _cents(float(we.mean())) if len(we) else "n/a"
        detail = (
            f"As a group: right side in {k} of {n} resolved markets, avg winning entry {avg}; "
            f"expected {exp:.1f} (p = {fmt_p(p_w)})"
        )
    else:
        detail = "No resolved markets yet"
    comps.append(_component("improbable_wins", "Group wins against the odds", pts, 35, detail))

    # 2. pooled timing --------------------------------------------------
    trials = timing_trials(bets, jx, pre_s)
    hits = [t for t in trials if t.hit]
    p_t = poisson_binomial_sf([t.q for t in trials], len(hits)) if hits else 1.0
    pts = points_from_p(p_t, 25) if hits else 0.0
    hit_usd = sum(t.hit_usd for t in hits)
    detail = (
        f"{len(hits)} of {len(trials)} group episodes landed just before a move their way "
        f"({_usd(hit_usd)} positioned; p = {fmt_p(p_t)})"
        if trials
        else "No bets in markets with detected price jumps"
    )
    comps.append(_component("pre_move_timing", "Bought before the news", pts, 25, detail))

    # 3. structural links ----------------------------------------------
    struct = [e for e in edges if set(e[2]["kinds"]) & STRUCTURAL]
    funders = Counter()
    for _, _, d in struct:
        for ev in d["evidence"]:
            if ev["kind"] in ("funding_shared", "withdrawal_shared") and ev.get("via"):
                funders[(ev["kind"], ev["via"])] += 1
    pts = 15 * min(1.0, len(struct) / max(size - 1, 1))
    if struct:
        top = funders.most_common(1)
        via = f"; most shared: {top[0][0][0].replace('_', ' ')} via {top[0][0][1][:10]}…" if top else ""
        detail = f"{len(struct)} of {len(edges)} links are on-chain money flows{via}"
    else:
        detail = "Linked by betting behaviour only (no shared money flows found)"
    comps.append(_component("structural", "Money flows between them", pts, 15, detail))

    # 4. freshness ------------------------------------------------------
    fresh = [m for m in members if (member_stats.get(m) or {}).get("max_fresh_bet", 0) >= 1000]
    pts = 10 * len(fresh) / size
    detail = f"{len(fresh)} of {size} wallets bet big within {a['fresh_wallet_hours']} h of being funded"
    comps.append(_component("fresh_wallets", "Freshly created wallets", pts, 10, detail))

    # 5. member intensity -----------------------------------------------
    top3 = sorted((member_scores.get(m, 0.0) for m in members), reverse=True)[:3]
    mean_top = float(np.mean(top3)) if top3 else 0.0
    pts = 15 * mean_top / 100
    comps.append(_component("members", "Individually suspicious", pts, 15,
                            f"Top member scores: {', '.join(f'{s:.0f}' for s in top3)}"))

    score = round(min(100.0, sum(c["points"] for c in comps)), 1)

    # ---- descriptive stats + top markets
    vol = float(bets["usd"].sum()) if len(bets) else 0.0
    pnl = sum((member_stats.get(m) or {}).get("pnl", 0.0) for m in members)
    top_markets = []
    if len(bets):
        g = bets.groupby("condition_id").agg(usd=("usd", "sum"), n_members=("wallet", "nunique"),
                                             dir=("dir", lambda s: int(np.sign(s.sum()) or 1)),
                                             first_ts=("ts", "min"))
        for m, r in g.sort_values(["n_members", "usd"], ascending=False).head(8).iterrows():
            mk = markets.get(m) or {}
            outcomes = mk.get("outcomes") or ["Yes", "No"]
            top_markets.append({
                "condition_id": m,
                "question": mk.get("question") or m[:12],
                "slug": mk.get("slug"),
                "n_members": int(r["n_members"]),
                "usd": round(float(r["usd"]), 2),
                "backed": outcomes[0] if r["dir"] > 0 else (outcomes[1] if len(outcomes) > 1 else "No"),
                "first_ts": int(r["first_ts"]),
            })
    stats = {
        "size": size,
        "volume": round(vol, 2),
        "pnl": round(pnl, 2),
        "resolved_markets": n,
        "group_wins": k,
        "expected_wins": round(exp, 2),
        "p_wins": p_w,
        "timing_trials": len(trials),
        "timing_hits": len(hits),
        "p_timing": p_t,
        "n_edges": len(edges),
        "n_structural": len(struct),
        "shared_addresses": [
            {"kind": kind, "address": addr, "links": c} for (kind, addr), c in funders.most_common(5)
        ],
        "first_ts": int(bets["ts"].min()) if len(bets) else None,
        "last_ts": int(bets["ts_last"].max()) if len(bets) else None,
        "fresh_members": len(fresh),
    }
    return score, comps, stats, top_markets
