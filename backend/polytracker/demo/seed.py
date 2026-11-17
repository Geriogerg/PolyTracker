"""Realistic synthetic dataset for demo mode and tests.

Actors (all addresses fake, deterministic):
  crowd      300 retail wallets funded through 4 exchange hot wallets,
             random timing, random sides. The null hypothesis.
  insiders   9 fresh wallets funded by one private address (itself funded by
             an exchange), buying the right side 1-5 h before each piece of
             news, cashing out to a common address. Modelled on the 2026
             Iran-strike case.
  trio       3 old wallets with no money link, betting together 7-10 h
             before news. Only behaviour can connect them.
  followers  5 wallets that pile in together right *after* news. Coordinated
             but not informed.
  farmers    12 sybil wallets funded by one address, dust bets. Coordinated,
             not informed.
  sharps     2 skilled traders buying favourites late. High win rate, not
             improbable.
"""
from __future__ import annotations

import hashlib
import logging
import math
import random
import time

import numpy as np
from sqlalchemy import delete

from ..clients.normalize import short_hash
from ..db import init_db, insert_ignore, session_scope, upsert
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

log = logging.getLogger(__name__)

DAY, HOUR, MIN = 86400, 3600, 60
PUSD = "0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb"
STEP = 30 * MIN

ADJ = ["Mean", "Quiet", "Bold", "Rapid", "Silent", "Lucky", "Sharp", "Grey", "Hidden", "Calm", "Wild", "Brave"]
NOUN = ["Record", "Falcon", "Harbor", "Signal", "Ledger", "Orchid", "Comet", "Atlas", "Cipher", "Delta", "Vector", "Mosaic"]


def addr(label: str) -> str:
    return "0x" + hashlib.sha1(label.encode()).hexdigest()[:40]


def cond(slug: str) -> str:
    return "0x" + hashlib.sha256(slug.encode()).hexdigest()


# (slug, question, tags, start_days_ago, jumps [(days_ago, new_level)], base, resolve (index, days_ago) | None)
MARKETS = [
    ("us-strike-iran-feb-28", "US military strike on Iran by February 28?", ["geopolitics", "middle-east"],
     150, [(120, 0.97)], 0.12, (0, 118)),
    ("israel-strikes-oil-terminals-mar-31", "Israel strikes Iranian oil terminals by March 31?", ["geopolitics", "middle-east"],
     130, [(95, 0.90)], 0.18, (0, 92)),
    ("strait-of-hormuz-closed-mar-15", "Strait of Hormuz closed before March 15?", ["geopolitics", "middle-east"],
     140, [], 0.32, (1, 100)),
    ("us-iran-ceasefire-apr-10", "US-Iran ceasefire announced by April 10?", ["geopolitics", "middle-east"],
     110, [(75, 0.92)], 0.09, (0, 73)),
    ("us-iran-nuclear-talks-may-1", "New US-Iran nuclear talks announced by May 1?", ["geopolitics", "politics"],
     95, [(55, 0.86)], 0.15, (0, 50)),
    ("khamenei-out-jun-30", "Khamenei out as Supreme Leader by June 30?", ["geopolitics", "middle-east"],
     90, [(40, 0.07)], 0.34, (1, 30)),
    ("fed-cut-june", "Fed cuts rates in June?", ["economy"],
     100, [], 0.55, (1, 35)),
    ("oil-above-100-dec", "Oil above $100 before December 31?", ["geopolitics", "economy"],
     80, [], 0.35, None),
    ("snap-french-legislative-2026", "Snap French legislative elections in 2026?", ["politics", "europe"],
     120, [], 0.22, None),
    ("iran-nuclear-deal-signed-2026", "US-Iran nuclear deal signed by December 31?", ["geopolitics", "politics"],
     60, [(3, 0.71)], 0.16, None),
]


class Market_:
    def __init__(self, spec, now: int, rng: np.random.Generator):
        slug, q, tags, start_d, jumps, base, resolve = spec
        self.slug, self.question, self.tags = slug, q, tags
        self.cid = cond(slug)
        self.tokens = [str(int(hashlib.sha256((slug + s).encode()).hexdigest()[:18], 16)) for s in ("yes", "no")]
        self.start = now - start_d * DAY
        self.resolve = resolve
        self.end = now - resolve[1] * DAY if resolve else now
        self.jumps = [(now - d * DAY + rng.integers(0, 12) * HOUR, lvl) for d, lvl in jumps]
        # random walk with jumps
        ts = np.arange(self.start, self.end + 1, STEP)
        p = np.empty(len(ts))
        level, x, since_jump = base, base, 99
        jump_iter = sorted(self.jumps)
        for i, t in enumerate(ts):
            for jt, lvl in jump_iter:
                if jt <= t < jt + STEP:
                    level, since_jump = lvl, 0
            rate = 0.65 if since_jump < 4 else 0.05
            since_jump += 1
            x += (level - x) * rate + rng.normal(0, 0.006)
            p[i] = min(max(x, 0.02), 0.98)
        if resolve:
            p[-1] = 0.999 if resolve[0] == 0 else 0.001
            p[-4:-1] = np.linspace(p[-5], p[-1], 5)[1:4]
        self.ts, self.p = ts, p
        self.resolved = bool(resolve)
        self.winning = resolve[0] if resolve else None

    def price0(self, t: float) -> float:
        return float(np.interp(t, self.ts, self.p))

    def active(self, t: float) -> bool:
        return self.start <= t <= self.end - HOUR


class Seeder:
    def __init__(self, now: int | None = None, seed: int = 7):
        self.now = now or int(time.time())
        self.rng = np.random.default_rng(seed)
        self.r = random.Random(seed)
        self.markets = [Market_(s, self.now, self.rng) for s in MARKETS]
        self.trades: list[dict] = []
        self.transfers: list[dict] = []
        self.names: dict[str, str] = {}
        self.labels: dict[str, str] = {}

    # ------------------------------------------------------------ helpers
    def m(self, slug: str) -> Market_:
        return next(x for x in self.markets if x.slug == slug)

    def name(self, w: str) -> None:
        if self.r.random() < 0.7:
            self.names[w] = f"{self.r.choice(ADJ)}-{self.r.choice(NOUN)}"

    def fund(self, src: str, dst: str, amount: float, t: float) -> None:
        tx = "0x" + short_hash("tx", src, dst, t, amount) + "0" * 24
        self.transfers.append({
            "uid": short_hash(tx, src, dst, amount), "tx_hash": tx, "token": PUSD,
            "from_addr": src, "to_addr": dst, "amount": round(amount, 2), "ts": int(t),
        })

    def bet(self, w: str, mk: Market_, outcome: int, usd: float, t: float, side: str = "BUY") -> None:
        t = min(t, mk.end - HOUR, self.now - 60)
        if t < mk.start:
            return
        fills = 1 if usd < 500 else self.r.randint(1, 3)
        for k in range(fills):
            tt = t + k * self.r.randint(30, 600)
            p0 = mk.price0(tt)
            px = p0 if outcome == 0 else 1 - p0
            px = min(max(px + self.rng.normal(0, 0.006), 0.01), 0.99)
            amt = usd / fills
            size = amt / px
            tx = "0x" + short_hash("trade", w, mk.cid, tt, k) + "0" * 24
            self.trades.append({
                "uid": short_hash(tx, w, outcome, side, round(size, 6), round(px, 6)),
                "wallet": w, "condition_id": mk.cid, "token_id": mk.tokens[outcome],
                "outcome": ["Yes", "No"][outcome], "outcome_index": outcome, "side": side,
                "price": round(px, 4), "size": round(size, 4), "usd": round(amt, 2), "ts": int(tt),
                "tx_hash": tx, "source": "demo",
            })

    def random_time(self, mk: Market_) -> float:
        return self.r.uniform(mk.start + HOUR, mk.end - 2 * HOUR)

    # -------------------------------------------------------------- actors
    def build(self) -> None:
        hubs = [addr(f"exchange-hot-{i}") for i in range(4)]
        for i, h in enumerate(hubs):
            self.labels[h] = f"Exchange hot wallet {'ABCD'[i]} (demo)"

        # crowd ----------------------------------------------------------
        for i in range(300):
            w = addr(f"crowd-{i}")
            self.name(w)
            hub = hubs[i % 4]
            t_fund = self.now - self.r.uniform(30, 400) * DAY
            self.fund(hub, w, self.r.uniform(200, 20000), t_fund)
            for _ in range(self.r.randint(3, 14)):
                mk = self.r.choice(self.markets)
                t = max(self.random_time(mk), t_fund + HOUR)
                if t >= mk.end - 2 * HOUR:
                    continue
                usd = math.exp(self.rng.normal(5.2, 1.2))
                self.bet(w, mk, self.r.randint(0, 1), min(usd, 15000), t)
            if self.r.random() < 0.5:  # cash out to a personal exchange deposit address
                self.fund(w, addr(f"deposit-{i}"), self.r.uniform(50, 3000), self.now - self.r.uniform(1, 25) * DAY)

        # insiders ------------------------------------------------------
        ring_funder = addr("ring-funder")
        cashout = addr("ring-cashout")
        first_event = min(t for mk in self.markets for t, _ in mk.jumps)
        self.fund(hubs[0], ring_funder, 400_000, first_event - 4 * DAY)
        insiders = [addr(f"insider-{i}") for i in range(9)]
        plays = [
            ("us-strike-iran-feb-28", 0), ("israel-strikes-oil-terminals-mar-31", 0),
            ("us-iran-ceasefire-apr-10", 0), ("us-iran-nuclear-talks-may-1", 0),
            ("khamenei-out-jun-30", 1), ("iran-nuclear-deal-signed-2026", 0),
        ]
        for i, w in enumerate(insiders):
            t_fund = first_event - self.r.uniform(20, 34) * HOUR
            self.fund(ring_funder, w, self.r.uniform(30_000, 60_000), t_fund)
            for slug, outcome in plays:
                mk = self.m(slug)
                jt = mk.jumps[0][0]
                right = outcome if self.r.random() < 0.97 else 1 - outcome
                self.bet(w, mk, right, self.r.uniform(5_000, 28_000), jt - self.r.uniform(1, 5) * HOUR)
            for _ in range(2):  # camouflage
                mk = self.r.choice([self.m("oil-above-100-dec"), self.m("snap-french-legislative-2026")])
                self.bet(w, mk, self.r.randint(0, 1), self.r.uniform(80, 400), self.random_time(mk))
            if i < 6:
                self.fund(w, cashout, self.r.uniform(20_000, 90_000), self.now - self.r.uniform(10, 25) * DAY)
        self.fund(insiders[0], insiders[3], 12_000, first_event + 10 * DAY)
        self.fund(insiders[5], insiders[7], 8_500, first_event + 40 * DAY)

        # trio: no money links, synchronised timing ---------------------
        trio = [addr(f"trio-{i}") for i in range(3)]
        for i, w in enumerate(trio):
            self.name(w)
            self.fund(hubs[(i + 1) % 4], w, self.r.uniform(40_000, 80_000), self.now - self.r.uniform(150, 260) * DAY)
        for slug, outcome in [("us-iran-ceasefire-apr-10", 0), ("us-iran-nuclear-talks-may-1", 0),
                              ("iran-nuclear-deal-signed-2026", 0), ("khamenei-out-jun-30", 1)]:
            mk = self.m(slug)
            anchor = mk.jumps[0][0] - self.r.uniform(7, 10) * HOUR
            for w in trio:
                self.bet(w, mk, outcome, self.r.uniform(3_000, 12_000), anchor + self.r.uniform(0, 20) * MIN)
        for w in trio:
            for _ in range(6):
                mk = self.r.choice(self.markets)
                self.bet(w, mk, self.r.randint(0, 1), self.r.uniform(200, 2_000), self.random_time(mk))

        # followers: pile in right after news ---------------------------
        followers = [addr(f"follower-{i}") for i in range(5)]
        for i, w in enumerate(followers):
            self.name(w)
            self.fund(hubs[1], w, self.r.uniform(5_000, 20_000), self.now - self.r.uniform(160, 300) * DAY)
        for slug in ["us-strike-iran-feb-28", "israel-strikes-oil-terminals-mar-31",
                     "us-iran-ceasefire-apr-10", "us-iran-nuclear-talks-may-1"]:
            mk = self.m(slug)
            anchor = mk.jumps[0][0] + self.r.uniform(1.5, 3) * HOUR
            for w in followers:
                self.bet(w, mk, 0, self.r.uniform(500, 3_000), anchor + self.r.uniform(0, 15) * MIN)

        # farmers: one funder, dust bets ------------------------------
        farm_funder = addr("farm-funder")
        self.fund(hubs[2], farm_funder, 2_000, self.now - 140 * DAY)
        for i in range(12):
            w = addr(f"farmer-{i}")
            t_fund = self.now - (130 - i * 0.2) * DAY
            self.fund(farm_funder, w, 100, t_fund)
            for _ in range(self.r.randint(6, 10)):
                mk = self.r.choice(self.markets)
                t = max(self.random_time(mk), t_fund + HOUR)
                if t < mk.end - 2 * HOUR:
                    self.bet(w, mk, self.r.randint(0, 1), self.r.uniform(5, 40), t)

        # sharps: favourites, late -------------------------------------
        for i in range(2):
            w = addr(f"sharp-{i}")
            self.names[w] = ["Kestrel", "Tidewater"][i]
            self.fund(hubs[3], w, 150_000, self.now - 330 * DAY)
            for mk in self.markets:
                t = mk.end - self.r.uniform(3, 12) * DAY
                if t < mk.start:
                    continue
                p0 = mk.price0(t)
                fav = 0 if p0 >= 0.5 else 1
                self.bet(w, mk, fav, self.r.uniform(2_000, 20_000), t)

    # -------------------------------------------------------------- write
    def write(self, reset: bool = True) -> dict:
        init_db()
        with session_scope() as s:
            if reset:
                for tbl in (WalletScore, Cluster, Edge, AnalysisRun, PriceJump, PricePoint, Trade, Transfer,
                            Wallet, Address, Market, Alert):
                    s.execute(delete(tbl))
            rows = []
            for mk in self.markets:
                p_last = float(mk.p[-1])
                rows.append({
                    "condition_id": mk.cid, "gamma_id": None, "slug": mk.slug, "question": mk.question,
                    "event_slug": mk.slug, "event_title": mk.question, "tags": mk.tags,
                    "outcomes": ["Yes", "No"], "token_ids": mk.tokens,
                    "volume": 0.0, "liquidity": 0.0, "last_price": round(p_last, 4),
                    "active": not mk.resolved, "closed": mk.resolved, "resolved": mk.resolved,
                    "winning_index": mk.winning, "end_ts": int(mk.end), "closed_ts": int(mk.end) if mk.resolved else None,
                    "tracked": True, "backfilled_at": self.now, "prices_synced_at": self.now, "updated_at": self.now,
                })
            vol = {}
            for t in self.trades:
                vol[t["condition_id"]] = vol.get(t["condition_id"], 0) + t["usd"]
            for r in rows:
                r["volume"] = round(vol.get(r["condition_id"], 0) * 40, 2)  # whole-market volume is larger than ours
            upsert(s, Market, rows, ["condition_id"])
            pts = [{"token_id": mk.tokens[0], "ts": int(t), "price": round(float(p), 4)}
                   for mk in self.markets for t, p in zip(mk.ts, mk.p)]
            insert_ignore(s, PricePoint, pts, ["token_id", "ts"])
            insert_ignore(s, Trade, self.trades, ["uid"])
            insert_ignore(s, Transfer, self.transfers, ["uid"])

            first_trade, last_trade = {}, {}
            for t in self.trades:
                w = t["wallet"]
                first_trade[w] = min(first_trade.get(w, t["ts"]), t["ts"])
                last_trade[w] = max(last_trade.get(w, t["ts"]), t["ts"])
            first_funded = {}
            for tr in self.transfers:
                if tr["to_addr"] in first_trade:
                    w = tr["to_addr"]
                    first_funded[w] = min(first_funded.get(w, tr["ts"]), tr["ts"])
            wallets = [{
                "address": w, "name": self.names.get(w), "pseudonym": None,
                "first_trade_ts": first_trade[w], "last_trade_ts": last_trade[w],
                "first_funded_ts": first_funded.get(w), "traced_at": self.now, "trace_status": "demo",
            } for w in first_trade]
            upsert(s, Wallet, wallets, ["address"])
            addrs = [{"address": a, "kind": "external", "n_counterparties": 0, "label": lbl, "traced_at": self.now}
                     for a, lbl in self.labels.items()]
            upsert(s, Address, addrs, ["address"])
        return {"markets": len(self.markets), "trades": len(self.trades), "transfers": len(self.transfers),
                "wallets": len({t["wallet"] for t in self.trades})}


def seed_demo(reset: bool = True) -> dict:
    sd = Seeder()
    sd.build()
    out = sd.write(reset=reset)
    log.info("demo data written: %s", out)
    return out


# --------------------------------------------------------- live simulator
class DemoTicker:
    """Keeps the demo feed alive: crowd trades on open markets, and now and
    then a large buy from a ring wallet so live alerts can fire."""

    def __init__(self):
        self.r = random.Random()
        self.open = [s for s in MARKETS if s[6] is None]
        self.n = 0

    def tick(self) -> list[dict]:
        from sqlalchemy import select

        self.n += 1
        now = int(time.time())
        with session_scope() as s:
            prices = {m.condition_id: (m.last_price or 0.5, m.token_ids) for m in
                      s.scalars(select(Market).where(Market.resolved.is_(False)))}
        if not prices:
            return []
        out = []
        k = 1 + (self.r.random() < 0.4)
        for _ in range(k):
            cid = self.r.choice(list(prices))
            p0, tokens = prices[cid]
            outcome = self.r.randint(0, 1)
            if self.n % 25 == 0:
                w = addr(f"insider-{self.r.randint(0, 8)}")
                usd = self.r.uniform(26_000, 60_000)
            else:
                w = addr(f"crowd-{self.r.randint(0, 299)}")
                usd = min(math.exp(self.r.gauss(5.0, 1.2)), 20_000)
            px = p0 if outcome == 0 else 1 - p0
            px = min(max(px + self.r.gauss(0, 0.01), 0.01), 0.99)
            tx = "0x" + short_hash("live", w, now, self.n, _) + "0" * 24
            out.append({
                "uid": short_hash(tx, w), "wallet": w, "condition_id": cid,
                "token_id": tokens[outcome] if tokens else None, "outcome": ["Yes", "No"][outcome],
                "outcome_index": outcome, "side": "BUY", "price": round(px, 4), "size": round(usd / px, 4),
                "usd": round(usd, 2), "ts": now, "tx_hash": tx, "source": "demo",
            })
        return out
