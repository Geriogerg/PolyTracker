"""Derived units of analysis.

* bets      : a wallet's trades on one outcome, same side, merged when
              closer than `merge_s` (one decision split into many fills
              should count once).
* positions : per wallet x resolved market, the outcome the wallet put the
              most money on, its entry price, and whether it won.
* episodes  : per wallet x market, bursts of betting separated by more than
              the pre-jump window; each episode is one timing "trial".
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .jumps import Jump
from .stats import interval_coverage

TRADE_COLS = ["wallet", "condition_id", "outcome_index", "side", "price", "size", "usd", "ts"]


def build_bets(trades: pd.DataFrame, merge_s: int) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame(
            columns=["wallet", "condition_id", "outcome_index", "side", "ts", "ts_last", "usd",
                     "shares", "n", "vwap", "dir", "p0_entry"]
        )
    keys = ["wallet", "condition_id", "outcome_index", "side"]
    df = trades.sort_values(keys + ["ts"]).reset_index(drop=True)
    gap = df.groupby(keys, sort=False)["ts"].diff()
    df["bet_no"] = (gap.isna() | (gap > merge_s)).cumsum()
    bets = df.groupby("bet_no", sort=False).agg(
        wallet=("wallet", "first"),
        condition_id=("condition_id", "first"),
        outcome_index=("outcome_index", "first"),
        side=("side", "first"),
        ts=("ts", "min"),
        ts_last=("ts", "max"),
        usd=("usd", "sum"),
        shares=("size", "sum"),
        n=("ts", "size"),
    ).reset_index(drop=True)
    bets["vwap"] = (bets["usd"] / bets["shares"]).clip(0.001, 0.999)
    is_buy = bets["side"] == "BUY"
    is_o0 = bets["outcome_index"] == 0
    bets["dir"] = np.where(is_buy == is_o0, 1, -1)  # +1 = bets on outcome 0 rising
    bets["p0_entry"] = np.where(is_o0, bets["vwap"], 1 - bets["vwap"])
    return bets


def build_positions(trades: pd.DataFrame, winners: dict[str, int], min_usd: float) -> pd.DataFrame:
    """One row per (wallet, resolved market)."""
    cols = ["wallet", "condition_id", "primary_outcome", "entry_p", "stake", "cost", "pnl", "won"]
    if trades.empty or not winners:
        return pd.DataFrame(columns=cols)
    res = trades[trades["condition_id"].isin(winners.keys())]
    if res.empty:
        return pd.DataFrame(columns=cols)
    k = ["wallet", "condition_id", "outcome_index"]
    buys = res[res["side"] == "BUY"].groupby(k).agg(buy_usd=("usd", "sum"), buy_sh=("size", "sum"))
    sells = res[res["side"] == "SELL"].groupby(k).agg(sell_usd=("usd", "sum"), sell_sh=("size", "sum"))
    pos = buys.join(sells, how="outer").fillna(0.0).reset_index()
    pos["winning"] = pos["condition_id"].map(winners)
    pos["net_sh"] = (pos["buy_sh"] - pos["sell_sh"]).clip(lower=0)
    pos["payout"] = np.where(pos["outcome_index"] == pos["winning"], pos["net_sh"], 0.0)
    pos["flow"] = pos["sell_usd"] + pos["payout"] - pos["buy_usd"]

    agg = pos.groupby(["wallet", "condition_id"]).agg(cost=("buy_usd", "sum"), pnl=("flow", "sum"))
    prim = pos.loc[pos.groupby(["wallet", "condition_id"])["buy_usd"].idxmax()].set_index(
        ["wallet", "condition_id"]
    )
    out = agg.join(prim[["outcome_index", "buy_usd", "buy_sh", "winning"]]).reset_index()
    out = out[out["buy_usd"] >= min_usd]
    out = out.rename(columns={"outcome_index": "primary_outcome", "buy_usd": "stake"})
    out["entry_p"] = (out["stake"] / out["buy_sh"]).clip(0.01, 0.99)
    out["won"] = out["primary_outcome"] == out["winning"]
    return out[cols].reset_index(drop=True)


# ----------------------------------------------------------------- timing
@dataclass
class JumpIndex:
    jumps: dict[str, list[Jump]]
    q: dict[str, float]  # chance a randomly-timed episode lands in a favourable pre-jump window


def build_jump_index(
    jumps: list[Jump], bets: pd.DataFrame, pre_s: int
) -> JumpIndex:
    by_m: dict[str, list[Jump]] = defaultdict(list)
    for j in jumps:
        by_m[j.condition_id].append(j)
    q: dict[str, float] = {}
    if not bets.empty:
        spans = bets.groupby("condition_id").agg(lo=("ts", "min"), hi=("ts_last", "max"))
    else:
        spans = pd.DataFrame(columns=["lo", "hi"])
    for m, js in by_m.items():
        if m in spans.index:
            lo, hi = int(spans.loc[m, "lo"]), int(spans.loc[m, "hi"])
        else:
            lo, hi = min(j.start_ts for j in js) - 30 * 86400, max(j.end_ts for j in js)
        lo = min(lo, min(j.start_ts for j in js) - pre_s)
        cov = interval_coverage([(j.start_ts - pre_s, j.start_ts) for j in js], (lo, max(hi, lo + 1)))
        # direction must also match: roughly half the windows favour a given side
        q[m] = float(min(max(0.5 * cov, 0.002), 0.5))
    return JumpIndex(dict(by_m), q)


def favourable_jump(bet, js: list[Jump], pre_s: int) -> Jump | None:
    """The jump this bet anticipated, if any: same direction, placed in the
    pre-jump window, at a price still near the pre-move level."""
    for j in js:
        if bet.dir != j.direction:
            continue
        if not (j.start_ts - pre_s <= bet.ts < j.start_ts):
            continue
        if (bet.p0_entry - j.p_before) * j.direction <= 0.5 * j.magnitude:
            return j
    return None


@dataclass
class TimingTrial:
    condition_id: str
    q: float
    hit: bool
    hit_usd: float
    jump_ts: int | None


def timing_trials(bets: pd.DataFrame, jx: JumpIndex, pre_s: int) -> list[TimingTrial]:
    """Episode-level trials for one wallet (or one pooled cluster)."""
    trials: list[TimingTrial] = []
    if bets.empty:
        return trials
    for m, grp in bets[bets["condition_id"].isin(jx.jumps.keys())].groupby("condition_id"):
        js = jx.jumps[m]
        grp = grp.sort_values("ts")
        ep_hit, ep_usd, ep_jump, last_ts = False, 0.0, None, None
        for b in grp.itertuples(index=False):
            if last_ts is not None and b.ts - last_ts > pre_s:
                trials.append(TimingTrial(m, jx.q[m], ep_hit, ep_usd, ep_jump))
                ep_hit, ep_usd, ep_jump = False, 0.0, None
            j = favourable_jump(b, js, pre_s)
            if j is not None:
                ep_hit = True
                ep_usd += float(b.usd)
                ep_jump = j.start_ts
            last_ts = b.ts_last
        trials.append(TimingTrial(m, jx.q[m], ep_hit, ep_usd, ep_jump))
    return trials


# --------------------------------------------------------------- behaviour
def behaviour_vectors(bets: pd.DataFrame) -> pd.DataFrame:
    """Standardised per-wallet behaviour fingerprint (used to reinforce links)."""
    if bets.empty:
        return pd.DataFrame()
    b = bets.copy()
    b["hour_bin"] = ((b["ts"] % 86400) // (4 * 3600)).astype(int)
    hours = pd.crosstab(b["wallet"], b["hour_bin"], normalize="index")
    hours = hours.reindex(columns=range(6), fill_value=0.0)
    hours.columns = [f"h{c}" for c in hours.columns]
    b["lusd"] = np.log10(b["usd"].clip(lower=1))
    g = b.groupby("wallet")
    feats = pd.DataFrame(
        {
            "lusd_mean": g["lusd"].mean(),
            "lusd_std": g["lusd"].std().fillna(0.0),
            "entry": g["vwap"].mean(),
            "buy_frac": g["side"].apply(lambda s: (s == "BUY").mean()),
            "longshot": g["vwap"].apply(lambda s: (s < 0.3).mean()),
            "markets": np.log10(g["condition_id"].nunique() + 1),
        }
    ).join(hours)
    std = feats.std().replace(0, 1.0)
    return (feats - feats.mean()) / std


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))
