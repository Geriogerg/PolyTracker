"""On-chain tracing: who funded each wallet, where did the money go.

Hop 1: every collateral transfer touching a tracked wallet.
Hop 2: the incoming transfers of each private funder found at hop 1, so two
wallets funded by different intermediaries that share a source still link.
An address with a full page of history or many distinct counterparties is
marked as a hub (exchange, bridge, router) and never walked through.
"""
from __future__ import annotations

import logging
import time

from sqlalchemy import func, or_, select

from ..clients.http import HttpError
from ..clients.onchain import ChainClient
from ..db import heartbeat, insert_ignore, session_scope, upsert
from ..models import Address, Trade, Transfer, Wallet

log = logging.getLogger(__name__)


def _wallets_to_trace(cfg: dict, limit: int) -> list[str]:
    oc = cfg["onchain"]
    now = int(time.time())
    with session_scope() as s:
        vol = (
            select(Trade.wallet, func.sum(Trade.usd).label("vol"))
            .group_by(Trade.wallet)
            .having(func.sum(Trade.usd) >= oc["min_wallet_volume_usd"])
            .subquery()
        )
        q = (
            select(vol.c.wallet)
            .join(Wallet, Wallet.address == vol.c.wallet)
            .where(
                or_(
                    Wallet.traced_at.is_(None),
                    # re-trace active wallets every 3 days to catch withdrawals
                    (Wallet.traced_at < now - 3 * 86400) & (Wallet.last_trade_ts > Wallet.traced_at),
                )
            )
            .order_by(vol.c.vol.desc())
            .limit(limit)
        )
        return list(s.scalars(q).all())


async def trace_cycle(chain: ChainClient, cfg: dict) -> dict:
    oc, a = cfg["onchain"], cfg["analysis"]
    tokens = set(oc["collateral_tokens"])
    system = set(oc["system_addresses"]) | set(oc["known_hubs"])
    budget = int(oc["max_traces_per_cycle"])
    stats = {"n_wallets_traced": 0, "n_funders_traced": 0, "n_transfers": 0, "n_hubs": 0}
    wallets = _wallets_to_trace(cfg, budget)
    if not wallets:
        return stats

    with session_scope() as s:
        tracked = set(s.scalars(select(Wallet.address)).all())
        known = {r.address: r for r in s.scalars(select(Address))}

    funders_to_walk: set[str] = set()
    now = int(time.time())
    for w in wallets:
        try:
            rows, _ = await chain.token_transfers(w)
        except HttpError as e:
            log.warning("trace %s: %s", w[:10], e)
            with session_scope() as s:
                ww = s.get(Wallet, w)
                if ww:
                    ww.trace_status = f"error: {str(e)[:30]}"
            continue
        rows = [r for r in rows if not tokens or r["token"] in tokens]
        incoming = [r for r in rows if r["to_addr"] == w and r["from_addr"] not in system]
        first_funded = min((r["ts"] for r in incoming), default=None)
        with session_scope() as s:
            stats["n_transfers"] += insert_ignore(s, Transfer, rows, ["uid"])
            ww = s.get(Wallet, w)
            if ww:
                ww.traced_at = now
                ww.trace_status = "ok"
                if first_funded:
                    ww.first_funded_ts = first_funded
        stats["n_wallets_traced"] += 1
        budget -= 1
        if int(oc["trace_depth"]) >= 2:
            for r in incoming:
                f = r["from_addr"]
                k = known.get(f)
                if f in tracked or f in system or (k and (k.kind != "external" or k.traced_at)):
                    continue
                funders_to_walk.add(f)

    # ---- hop 2
    for f in list(funders_to_walk)[: max(budget, 0)]:
        try:
            rows, truncated = await chain.token_transfers(f)
        except HttpError as e:
            log.warning("trace funder %s: %s", f[:10], e)
            continue
        rows = [r for r in rows if not tokens or r["token"] in tokens]
        counterparties = {r["to_addr"] for r in rows if r["from_addr"] == f} | {
            r["from_addr"] for r in rows if r["to_addr"] == f
        }
        is_hub = truncated or len(counterparties) >= int(a["hub_global_recipients"])
        with session_scope() as s:
            upsert(s, Address, [{
                "address": f, "kind": "hub" if is_hub else "external",
                "n_counterparties": len(counterparties), "label": None, "traced_at": now,
            }], ["address"], ["kind", "n_counterparties", "traced_at"])
            if not is_hub:
                # keep its funding (to walk the second hop) and its payouts to tracked wallets
                keep = [r for r in rows if r["to_addr"] == f or r["to_addr"] in tracked]
                stats["n_transfers"] += insert_ignore(s, Transfer, keep, ["uid"])
        stats["n_funders_traced"] += 1
        stats["n_hubs"] += int(is_hub)
    heartbeat("tracer", **stats)
    return stats
