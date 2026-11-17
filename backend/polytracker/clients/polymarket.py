"""Polymarket public APIs (no auth needed).

  Gamma  https://gamma-api.polymarket.com   markets, events, tags
  Data   https://data-api.polymarket.com    trades (v2 keyset feed, v1 fallback)
  CLOB   https://clob.polymarket.com        /prices-history
  RTDS   wss://ws-live-data.polymarket.com  live trade feed (activity/trades)
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import AsyncIterator, Awaitable, Callable

import websockets

from ..config import env
from .http import Http, HttpError
from .normalize import normalize_market, normalize_trade

log = logging.getLogger(__name__)


class PolymarketClient:
    def __init__(self, http: Http):
        self.http = http
        self.gamma = env.gamma_url.rstrip("/")
        self.data = env.data_url.rstrip("/")
        self.clob = env.clob_url.rstrip("/")
        self._v2_ok: bool | None = None  # learned at runtime

    # ------------------------------------------------------------ markets
    async def events_by_tag(self, tag_slug: str, closed: bool, max_events: int = 500) -> list[dict]:
        """Events (with nested markets) for a tag slug, biggest volume first."""
        out: list[dict] = []
        offset, page = 0, 100
        while len(out) < max_events:
            data = await self.http.get_json(
                f"{self.gamma}/events",
                {
                    "tag_slug": tag_slug,
                    "closed": str(closed).lower(),
                    "limit": page,
                    "offset": offset,
                    "order": "volume",
                    "ascending": "false",
                },
            )
            if not isinstance(data, list) or not data:
                break
            out.extend(data)
            if len(data) < page:
                break
            offset += page
        return out[:max_events]

    async def top_markets(self, closed: bool, limit: int = 200) -> list[dict]:
        out: list[dict] = []
        offset = 0
        while len(out) < limit:
            data = await self.http.get_json(
                f"{self.gamma}/markets",
                {
                    "closed": str(closed).lower(),
                    "limit": 100,
                    "offset": offset,
                    "order": "volumeNum",
                    "ascending": "false",
                },
            )
            if not isinstance(data, list) or not data:
                break
            out.extend(data)
            if len(data) < 100:
                break
            offset += 100
        return out[:limit]

    async def market_by_ref(self, ref: str) -> dict | None:
        """Look up one market by slug or 0x condition id."""
        params = {"condition_ids": ref} if ref.startswith("0x") else {"slug": ref}
        data = await self.http.get_json(f"{self.gamma}/markets", params)
        if isinstance(data, list) and data:
            return data[0]
        return None

    async def scoped_markets(self, scope: dict) -> list[dict]:
        """Resolve the configured scope into normalized market rows."""
        rows: dict[str, dict] = {}
        now = time.time()
        closed_cutoff = now - float(scope.get("include_closed_days", 0)) * 86400
        mode = scope.get("mode", "tags")

        def keep(r: dict | None) -> bool:
            if not r:
                return False
            q = (r["question"] + " " + (r.get("event_title") or "")).lower()
            inc = [k.lower() for k in scope.get("include_keywords") or []]
            exc = [k.lower() for k in scope.get("exclude_keywords") or []]
            if inc and not any(k in q for k in inc):
                return False
            if any(k in q for k in exc):
                return False
            if r["volume"] < float(scope.get("min_volume", 0)):
                return False
            if r["closed"] and (r.get("closed_ts") or 0) < closed_cutoff:
                return False
            return True

        if mode == "tags":
            for tag in scope.get("tags") or []:
                for closed in (False, True):
                    if closed and not scope.get("include_closed_days"):
                        continue
                    try:
                        events = await self.events_by_tag(tag, closed=closed, max_events=300)
                    except HttpError as e:
                        log.warning("tag %s: %s", tag, e)
                        continue
                    for ev in events:
                        for m in ev.get("markets") or []:
                            r = normalize_market(m, ev)
                            if keep(r):
                                rows[r["condition_id"]] = r
        elif mode == "all":
            for closed in (False, True):
                if closed and not scope.get("include_closed_days"):
                    continue
                for m in await self.top_markets(closed=closed, limit=int(scope.get("max_markets", 150)) * 2):
                    r = normalize_market(m)
                    if keep(r):
                        rows[r["condition_id"]] = r

        for ref in scope.get("explicit_markets") or []:
            try:
                m = await self.market_by_ref(str(ref).strip())
            except HttpError as e:
                log.warning("explicit market %s: %s", ref, e)
                continue
            r = normalize_market(m) if m else None
            if r:
                r["_explicit"] = True
                rows[r["condition_id"]] = r

        ranked = sorted(rows.values(), key=lambda r: (not r.get("_explicit"), -r["volume"]))
        limit = int(scope.get("max_markets", 150))
        explicit = [r for r in ranked if r.get("_explicit")]
        others = [r for r in ranked if not r.get("_explicit")][: max(0, limit - len(explicit))]
        return [{k: v for k, v in r.items() if not k.startswith("_")} for r in explicit + others]

    # ------------------------------------------------------------- trades
    async def _trades_v2(self, params: dict) -> tuple[list[dict], str | None]:
        data = await self.http.get_json(f"{self.data}/v2/trades", params)
        rows = (data.get("data") or []) if isinstance(data, dict) else []
        cursor = (data.get("pagination") or {}).get("next_cursor") if isinstance(data, dict) else None
        return rows, cursor

    async def _trades_v1(self, params: dict, offset: int) -> list[dict]:
        p = {
            "limit": min(int(params.get("limit", 500)), 1000),
            "offset": offset,
            "takerOnly": str(params.get("taker_only", "true")).lower(),
        }
        if params.get("condition"):
            p["market"] = params["condition"]
        if params.get("user"):
            p["user"] = params["user"]
        if params.get("filter_amount"):
            p["filterType"] = "CASH"
            p["filterAmount"] = params["filter_amount"]
        data = await self.http.get_json(f"{self.data}/trades", p)
        return data if isinstance(data, list) else []

    async def iter_trades(
        self,
        *,
        conditions: list[str] | None = None,
        user: str | None = None,
        max_pages: int = 10,
        page_size: int = 1000,
        min_usd: float = 0,
        stop_before_ts: int | None = None,
        source: str = "poll",
        taker_only: bool = False,
    ) -> AsyncIterator[list[dict]]:
        """Yield pages of normalized trades, newest first.

        Uses the v2 keyset feed; falls back to v1 offset paging if v2 is
        unavailable. Stops early once a page is entirely older than
        `stop_before_ts` (incremental polling).

        `taker_only=False` (default) also returns maker fills: a wallet that
        trades with limit orders would otherwise be invisible.
        """
        params: dict = {"limit": page_size, "taker_only": str(taker_only).lower()}
        if conditions:
            params["condition"] = ",".join(conditions[:20])
        if user:
            params["user"] = user
        if min_usd:
            params["filter_type"] = "CASH"
            params["filter_amount"] = min_usd

        use_v2 = self._v2_ok is not False
        cursor = None
        offset = 0
        for _ in range(max_pages):
            raw: list[dict]
            if use_v2:
                try:
                    q = dict(params)
                    if cursor:
                        q = {k: v for k, v in q.items() if k != "limit"}
                        q["cursor"] = cursor
                    raw, cursor = await self._trades_v2(q)
                    self._v2_ok = True
                except HttpError as e:
                    if e.status in (404, 410) or (e.status == 400 and self._v2_ok is None):
                        log.warning("Data API v2 unavailable (%s), switching to v1", e.status)
                        self._v2_ok = False
                        use_v2 = False
                        raw = await self._trades_v1(params, offset)
                    else:
                        raise
            else:
                if offset >= 10000:
                    break
                raw = await self._trades_v1(params, offset)
            offset += len(raw)
            page = [t for t in (normalize_trade(r, source) for r in raw) if t]
            if page:
                yield page
            if not raw:
                break
            if use_v2 and not cursor:
                break
            if not use_v2 and len(raw) < params["limit"]:
                break
            if stop_before_ts and page and max(t["ts"] for t in page) < stop_before_ts:
                break

    # ------------------------------------------------------------- prices
    async def price_history(
        self, token_id: str, start_ts: int, end_ts: int, fidelity: int = 10
    ) -> list[tuple[int, float]]:
        """CLOB price history, fetched in 14-day windows (fine fidelity is range-limited)."""
        out: list[tuple[int, float]] = []
        window = 14 * 86400
        t = start_ts
        while t < end_ts:
            t2 = min(end_ts, t + window)
            try:
                data = await self.http.get_json(
                    f"{self.clob}/prices-history",
                    {"market": token_id, "startTs": t, "endTs": t2, "fidelity": fidelity},
                )
            except HttpError as e:
                log.debug("price history %s: %s", token_id[:12], e)
                data = {}
            for pt in (data or {}).get("history") or []:
                try:
                    out.append((int(pt["t"]), float(pt["p"])))
                except (KeyError, TypeError, ValueError):
                    continue
            t = t2
        return out

    # ----------------------------------------------------------- realtime
    async def stream_trades(
        self,
        on_trade: Callable[[dict], Awaitable[None]],
        stop: asyncio.Event,
        idle_timeout: float = 90.0,
    ) -> None:
        """Subscribe to the RTDS global trade feed until `stop` is set.

        Reconnects with backoff. A watchdog forces a reconnect when the
        socket stays silent for `idle_timeout` seconds (the feed is known to
        stall while the TCP connection still looks healthy).
        """
        backoff = 1.0
        sub = json.dumps({"action": "subscribe", "subscriptions": [{"topic": "activity", "type": "trades"}]})
        while not stop.is_set():
            try:
                async with websockets.connect(env.rtds_url, ping_interval=None, open_timeout=15) as ws:
                    await ws.send(sub)
                    log.info("RTDS connected")
                    backoff = 1.0
                    last_msg = time.monotonic()

                    async def pinger():
                        while True:
                            await asyncio.sleep(5)
                            await ws.send("PING")

                    ping_task = asyncio.create_task(pinger())
                    try:
                        while not stop.is_set():
                            try:
                                msg = await asyncio.wait_for(ws.recv(), timeout=5)
                            except asyncio.TimeoutError:
                                if time.monotonic() - last_msg > idle_timeout:
                                    log.warning("RTDS idle for %.0fs, reconnecting", idle_timeout)
                                    break
                                continue
                            last_msg = time.monotonic()
                            if not isinstance(msg, str) or not msg.startswith("{"):
                                continue
                            try:
                                data = json.loads(msg)
                            except json.JSONDecodeError:
                                continue
                            if data.get("topic") != "activity":
                                continue
                            payload = data.get("payload")
                            items = payload if isinstance(payload, list) else [payload]
                            for p in items:
                                if isinstance(p, dict):
                                    t = normalize_trade(p, source="ws")
                                    if t:
                                        await on_trade(t)
                    finally:
                        ping_task.cancel()
            except Exception as exc:  # network errors, handshake failures
                log.warning("RTDS error: %s", exc)
            if not stop.is_set():
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
