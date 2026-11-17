"""Live ingestion paths against mocked Polymarket/Etherscan/RTDS endpoints."""
import asyncio
import json

import httpx
import pytest
import websockets
from sqlalchemy import func, select

from polytracker.clients.http import Http
from polytracker.clients.onchain import ChainClient
from polytracker.clients.polymarket import PolymarketClient
from polytracker.db import session_scope, set_setting
from polytracker.models import Address, Market, PricePoint, Trade, Transfer, Wallet
from polytracker.workers.main import Worker
from tests.fixtures.api_samples import (
    COND_EXPLICIT,
    COND_OPEN,
    COND_RESOLVED,
    FUNDER,
    W1,
    W2,
    MockPolymarket,
)


def run(coro):
    return asyncio.run(coro)


def test_scope_resolution_tags_keywords_explicit():
    async def go():
        pm = PolymarketClient(Http(transport=httpx.MockTransport(MockPolymarket()), default_rps=1000))
        rows = await pm.scoped_markets({
            "mode": "tags", "tags": ["geopolitics"], "exclude_keywords": ["nfl"], "explicit_markets": ["explicit"],
            "min_volume": 1000, "max_markets": 10, "include_closed_days": 36500,
        })
        await pm.http.aclose()
        return {r["condition_id"]: r for r in rows}

    rows = run(go())
    assert COND_OPEN in rows and COND_RESOLVED in rows
    assert rows[COND_RESOLVED]["resolved"] and rows[COND_RESOLVED]["winning_index"] == 0
    assert rows[COND_EXPLICIT]["question"] == "Explicit market?"  # explicit beats the keyword filter
    assert rows[COND_OPEN]["tags"] == ["geopolitics"]


def test_v2_cursor_pagination_and_v1_fallback():
    async def collect(v2):
        mock = MockPolymarket(v2_available=v2)
        pm = PolymarketClient(Http(transport=httpx.MockTransport(mock), default_rps=1000))
        out = []
        async for page in pm.iter_trades(conditions=[COND_OPEN], max_pages=5):
            out += page
        await pm.http.aclose()
        return out, mock.calls

    trades, calls = run(collect(True))
    assert len(trades) == 3 and {t["wallet"] for t in trades} == {W1, W2}
    assert any("cursor=abc" in c for c in calls), "must follow next_cursor"
    assert all("taker_only=false" in c for c in calls if "/v2/trades" in c), "maker fills must be included"
    trades, calls = run(collect(False))
    assert len(trades) == 1 and trades[0]["outcome_index"] == 1
    assert any("data-api.polymarket.com/trades" in c for c in calls)


def test_worker_cycles_end_to_end(db):
    async def go():
        w = Worker(http=Http(transport=httpx.MockTransport(MockPolymarket()), default_rps=1000))
        with session_scope() as s:
            set_setting(s, "config_overrides", {"scope": {
                "mode": "tags", "tags": ["geopolitics"], "exclude_keywords": ["nfl"], "explicit_markets": [],
                "include_keywords": [], "min_volume": 1000, "max_markets": 10, "include_closed_days": 36500}})
        await w.market_sync()
        await w.backfill()
        await w.poll()
        await w.prices()
        chain = ChainClient(w.http, "etherscan", 137, 1000)
        await w.tracer(chain)
        await w.http.aclose()

    run(go())
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(Market).where(Market.tracked)) == 2
        assert s.scalar(select(func.count()).select_from(Trade)) == 6  # 3 per market, deduplicated across backfill+poll
        assert s.scalar(select(func.count()).select_from(PricePoint)) > 0
        w1 = s.get(Wallet, W1)
        assert w1.name == "Quiet-Falcon" and w1.first_funded_ts and w1.trace_status == "ok"
        # the shared private funder was walked one hop further up
        assert s.get(Address, FUNDER).traced_at
        assert s.scalar(select(func.count()).select_from(Transfer).where(Transfer.to_addr == FUNDER)) == 1
        assert s.scalars(select(Market).where(Market.condition_id == COND_OPEN)).one().backfilled_at


def test_rtds_stream_parses_trades():
    received = []

    async def go():
        async def handler(ws):
            sub = json.loads(await ws.recv())
            assert sub["action"] == "subscribe" and sub["subscriptions"][0]["topic"] == "activity"
            await ws.send(json.dumps({"topic": "activity", "type": "trades", "timestamp": 1, "payload": {
                "proxyWallet": W1, "conditionId": COND_OPEN, "asset": "111", "side": "BUY", "size": 10,
                "price": 0.4, "timestamp": 1782752879000, "outcome": "Yes", "outcomeIndex": 0,
                "transactionHash": "0x" + "7" * 64}}))
            await ws.send("PONG")
            await asyncio.sleep(1)

        async with websockets.serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            import polytracker.clients.polymarket as pmod

            stop = asyncio.Event()
            orig = pmod.env.__class__.rtds_url
            pmod.env.__class__.rtds_url = property(lambda self: f"ws://127.0.0.1:{port}")
            try:
                async def on_trade(t):
                    received.append(t)
                    stop.set()

                pm = PolymarketClient(Http())
                await asyncio.wait_for(pm.stream_trades(on_trade, stop), timeout=10)
                await pm.http.aclose()
            finally:
                pmod.env.__class__.rtds_url = orig

    run(go())
    assert received and received[0]["source"] == "ws" and received[0]["ts"] == 1782752879


def test_http_retries_on_429():
    n = {"calls": 0}

    def handler(req):
        n["calls"] += 1
        if n["calls"] < 3:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": "busy"})
        return httpx.Response(200, json={"ok": True})

    async def go():
        h = Http(transport=httpx.MockTransport(handler), default_rps=1000)
        r = await h.get_json("https://data-api.polymarket.com/v2/trades")
        await h.aclose()
        return r

    assert run(go()) == {"ok": True} and n["calls"] == 3
