"""Write paths shared by the ingestion workers."""
from __future__ import annotations

import time

from sqlalchemy import select

from ..clients.normalize import strip_private
from ..db import insert_ignore, session_scope, upsert
from ..models import Market, Trade, Wallet


def store_trades(rows: list[dict], min_usd: float = 0.0) -> list[dict]:
    """Insert trades (dedup on uid), maintain wallet first/last-seen and
    market last price. Returns the rows that were actually new."""
    rows = [r for r in rows if r["usd"] >= min_usd]
    if not rows:
        return []
    by_uid = {r["uid"]: r for r in rows}
    with session_scope() as s:
        existing = set(s.scalars(select(Trade.uid).where(Trade.uid.in_(list(by_uid)))).all())
        new = [r for uid, r in by_uid.items() if uid not in existing]
        if not new:
            return []
        insert_ignore(s, Trade, [strip_private(r) for r in new], ["uid"])

        # wallets: first/last trade, profile names
        agg: dict[str, dict] = {}
        for r in new:
            a = agg.setdefault(r["wallet"], {"first": r["ts"], "last": r["ts"], "name": None, "pseudo": None})
            a["first"] = min(a["first"], r["ts"])
            a["last"] = max(a["last"], r["ts"])
            a["name"] = a["name"] or r.get("_name")
            a["pseudo"] = a["pseudo"] or r.get("_pseudonym")
        known = {w.address: w for w in s.scalars(select(Wallet).where(Wallet.address.in_(list(agg))))}
        wrows = []
        for addr, a in agg.items():
            w = known.get(addr)
            wrows.append({
                "address": addr,
                "name": a["name"] or (w.name if w else None),
                "pseudonym": a["pseudo"] or (w.pseudonym if w else None),
                "first_trade_ts": min(a["first"], w.first_trade_ts or a["first"]) if w else a["first"],
                "last_trade_ts": max(a["last"], w.last_trade_ts or a["last"]) if w else a["last"],
            })
        upsert(s, Wallet, wrows, ["address"], ["name", "pseudonym", "first_trade_ts", "last_trade_ts"])

        # live price of outcome 0 from the newest trade per market
        latest: dict[str, dict] = {}
        for r in new:
            if r["source"] in ("backfill",):
                continue
            cur = latest.get(r["condition_id"])
            if cur is None or r["ts"] > cur["ts"]:
                latest[r["condition_id"]] = r
        now = int(time.time())
        for cid, r in latest.items():
            m = s.get(Market, cid)
            if m is not None:
                m.last_price = r["price"] if r["outcome_index"] == 0 else round(1 - r["price"], 4)
                m.updated_at = now
    return new
