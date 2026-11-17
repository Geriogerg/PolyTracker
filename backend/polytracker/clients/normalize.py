"""Turn raw API payloads into rows for our tables.

Handles the three trade shapes Polymarket serves:
  * Data API v2  (/v2/trades)     snake_case: proxy_wallet, condition_id, token_id…
  * Data API v1  (/trades)        camelCase:  proxyWallet, conditionId, asset…
  * RTDS socket  (activity/trades) camelCase payload, sometimes ms timestamps
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


def _first(d: dict, *keys, default=None):
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return default


def _as_list(v) -> list:
    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        try:
            parsed = json.loads(v)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return [s.strip() for s in v.split(",") if s.strip()]
    return []


def _ts(v) -> int | None:
    """Epoch seconds from int/float/str (s or ms) or ISO-8601."""
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        x = float(v)
        return int(x / 1000) if x > 1e12 else int(x)
    s = str(v).strip()
    if s.replace(".", "", 1).isdigit():
        return _ts(float(s))
    try:
        s = s.replace("Z", "+00:00")
        if " " in s and "T" not in s:
            s = s.replace(" ", "T", 1)
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except ValueError:
        return None


def short_hash(*parts: Any) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()


# ---------------------------------------------------------------- trades
def normalize_trade(raw: dict, source: str = "poll") -> dict | None:
    wallet = _first(raw, "proxy_wallet", "proxyWallet", "user", "maker")
    cond = _first(raw, "condition_id", "conditionId", "market")
    if not wallet or not cond:
        return None
    try:
        price = float(_first(raw, "price", default=0))
        size = float(_first(raw, "size", default=0))
    except (TypeError, ValueError):
        return None
    if price <= 0 or size <= 0:
        return None
    ts = _ts(_first(raw, "timestamp", "match_time", "matchTime"))
    if ts is None:
        return None
    wallet = wallet.lower()
    token = str(_first(raw, "token_id", "asset", "asset_id", default="") or "")
    side = str(_first(raw, "side", default="BUY")).upper()
    tx = str(_first(raw, "transaction_hash", "transactionHash", default="") or "").lower()
    oi = _first(raw, "outcome_index", "outcomeIndex", default=0)
    try:
        oi = int(oi)
    except (TypeError, ValueError):
        oi = 0
    if oi == 999:  # API sentinel: outcome not labelled
        oi = 0
    usd = _first(raw, "usdc_size", "usdcSize")
    usd = float(usd) if usd not in (None, "") else price * size
    return {
        "uid": short_hash(tx or ts, wallet, token, side, round(size, 6), round(price, 6)),
        "wallet": wallet,
        "condition_id": cond.lower(),
        "token_id": token or None,
        "outcome": _first(raw, "outcome"),
        "outcome_index": oi,
        "side": "SELL" if side == "SELL" else "BUY",
        "price": price,
        "size": size,
        "usd": round(usd, 6),
        "ts": ts,
        "tx_hash": tx or None,
        "source": source,
        # profile fields, consumed by the wallet upsert (not stored on trades)
        "_name": _first(raw, "name"),
        "_pseudonym": _first(raw, "pseudonym"),
        "_slug": _first(raw, "slug"),
        "_event_slug": _first(raw, "event_slug", "eventSlug"),
        "_title": _first(raw, "title"),
    }


def strip_private(row: dict) -> dict:
    return {k: v for k, v in row.items() if not k.startswith("_")}


# --------------------------------------------------------------- markets
def normalize_market(m: dict, event: dict | None = None) -> dict | None:
    cond = _first(m, "conditionId", "condition_id")
    if not cond:
        return None
    outcomes = [str(o) for o in _as_list(m.get("outcomes"))] or ["Yes", "No"]
    prices = []
    for p in _as_list(m.get("outcomePrices")):
        try:
            prices.append(float(p))
        except (TypeError, ValueError):
            prices.append(None)
    tokens = [str(t) for t in _as_list(m.get("clobTokenIds"))]
    closed = bool(m.get("closed"))
    resolved, winning = False, None
    valid = [p for p in prices if p is not None]
    if closed and len(valid) >= 2 and max(valid) >= 0.99 and min(valid) <= 0.01:
        resolved = True
        winning = prices.index(max(valid))
    tag_src = (event or {}).get("tags") or m.get("tags") or []
    tags = sorted({(t.get("slug") or t.get("label") or "").lower() for t in tag_src if isinstance(t, dict)} - {""})
    vol = _first(m, "volumeNum", "volume", default=0)
    liq = _first(m, "liquidityNum", "liquidity", default=0)
    try:
        vol = float(vol)
    except (TypeError, ValueError):
        vol = 0.0
    try:
        liq = float(liq)
    except (TypeError, ValueError):
        liq = 0.0
    last = prices[0] if prices and prices[0] is not None else None
    if last is None and m.get("lastTradePrice") is not None:
        try:
            last = float(m["lastTradePrice"])
        except (TypeError, ValueError):
            last = None
    return {
        "condition_id": cond.lower(),
        "gamma_id": str(m.get("id")) if m.get("id") is not None else None,
        "slug": m.get("slug"),
        "question": m.get("question") or m.get("title") or "",
        "event_slug": (event or {}).get("slug") or _event_field(m, "slug"),
        "event_title": (event or {}).get("title") or _event_field(m, "title"),
        "tags": tags,
        "outcomes": outcomes,
        "token_ids": tokens,
        "volume": vol,
        "liquidity": liq,
        "last_price": last,
        "active": bool(m.get("active", True)),
        "closed": closed,
        "resolved": resolved,
        "winning_index": winning,
        "end_ts": _ts(m.get("endDate") or m.get("endDateIso")),
        "closed_ts": _ts(m.get("closedTime")) or (_ts(m.get("umaEndDate")) if closed else None),
    }


def _event_field(m: dict, key: str):
    evs = m.get("events") or []
    if evs and isinstance(evs[0], dict):
        return evs[0].get(key)
    return None


# -------------------------------------------------------------- transfers
def normalize_transfer(raw: dict) -> dict | None:
    """Etherscan/Blockscout `tokentx` row."""
    try:
        decimals = int(raw.get("tokenDecimal") or 6)
        amount = int(raw.get("value") or 0) / (10**decimals)
    except (TypeError, ValueError):
        return None
    frm = (raw.get("from") or "").lower()
    to = (raw.get("to") or "").lower()
    tx = (raw.get("hash") or "").lower()
    if not frm or not to or not tx:
        return None
    return {
        "uid": short_hash(tx, raw.get("logIndex", ""), frm, to, raw.get("value")),
        "tx_hash": tx,
        "token": (raw.get("contractAddress") or "").lower(),
        "from_addr": frm,
        "to_addr": to,
        "amount": amount,
        "ts": _ts(raw.get("timeStamp")) or 0,
    }
