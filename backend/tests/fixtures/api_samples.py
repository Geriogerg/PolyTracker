"""Payloads shaped like the real Polymarket / Etherscan responses (Nov 2026 docs)."""
import json
import time

import httpx

NOW = int(time.time())
COND_OPEN = "0x" + "a1" * 32
COND_RESOLVED = "0x" + "b2" * 32
COND_EXPLICIT = "0x" + "c3" * 32
TOK = {COND_OPEN: ["111", "112"], COND_RESOLVED: ["221", "222"], COND_EXPLICIT: ["331", "332"]}
W1 = "0x" + "1" * 40
W2 = "0x" + "2" * 40
FUNDER = "0x" + "f" * 40
PUSD = "0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb"


def gamma_market(cond, q, closed=False, prices=("0.35", "0.65"), volume=120000.0, slug=None):
    return {
        "id": str(abs(hash(cond)) % 100000),
        "question": q,
        "conditionId": cond,
        "slug": slug or q.lower().replace(" ", "-").strip("?"),
        "outcomes": json.dumps(["Yes", "No"]),
        "outcomePrices": json.dumps(list(prices)),
        "clobTokenIds": json.dumps(TOK[cond]),
        "volumeNum": volume,
        "liquidityNum": 5000.0,
        "active": not closed,
        "closed": closed,
        "endDate": "2026-12-31T00:00:00Z",
        "closedTime": "2026-10-01 12:00:00+00" if closed else None,
    }


EVENTS_OPEN = [{
    "id": "9001", "slug": "iran-deal", "title": "Iran deal",
    "tags": [{"id": "1", "slug": "geopolitics", "label": "Geopolitics"}],
    "markets": [
        gamma_market(COND_OPEN, "US-Iran deal signed by December 31?"),
        {**gamma_market(COND_EXPLICIT, "NFL: who wins?"), "volumeNum": 999999.0},  # excluded by keyword
    ],
}]
EVENTS_CLOSED = [{
    "id": "9002", "slug": "strike", "title": "Strike",
    "tags": [{"id": "1", "slug": "geopolitics", "label": "Geopolitics"}],
    "markets": [gamma_market(COND_RESOLVED, "US strike on Iran by Feb 28?", closed=True, prices=("1", "0"))],
}]


def v2_trade(wallet, cond, ts, side="BUY", oi=0, price=0.2, size=6000.0, tx=None):
    return {
        "proxy_wallet": wallet, "side": side, "token_id": TOK[cond][oi], "condition_id": cond,
        "size": size, "price": price, "timestamp": ts, "title": "t", "slug": "s", "icon": "",
        "event_slug": "e", "outcome": ["Yes", "No"][oi], "outcome_index": oi, "name": "Quiet-Falcon",
        "pseudonym": "Quiet-Falcon", "bio": "", "profile_image": "", "profile_image_optimized": "",
        "transaction_hash": tx or f"0x{abs(hash((wallet, ts, side))):064x}"[:66],
    }


class MockPolymarket:
    """httpx.MockTransport handler routing like the real hosts."""

    def __init__(self, v2_available=True):
        self.v2_available = v2_available
        self.calls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        host, path, q = request.url.host, request.url.path, dict(request.url.params)
        self.calls.append(f"{host}{path}?{request.url.query.decode()}")
        if host == "gamma-api.polymarket.com":
            if path == "/events":
                if q.get("offset", "0") != "0":
                    return httpx.Response(200, json=[])
                return httpx.Response(200, json=EVENTS_CLOSED if q.get("closed") == "true" else EVENTS_OPEN)
            if path == "/markets":
                return httpx.Response(200, json=[gamma_market(COND_EXPLICIT, "Explicit market?", slug="explicit")])
        if host == "data-api.polymarket.com":
            if path == "/v2/trades":
                if not self.v2_available:
                    return httpx.Response(404, json={"error": "not found"})
                cond = q.get("condition", "").split(",")[0]
                if "cursor" in q:
                    rows = [v2_trade(W2, cond, NOW - 7200, price=0.18)]
                    return httpx.Response(200, json={"data": rows, "pagination": {"has_more": False, "next_cursor": None, "limit": 2, "offset": 2}})
                rows = [v2_trade(W1, cond, NOW - 3600, price=0.21), v2_trade(W1, cond, NOW - 3500, side="SELL", price=0.3, size=100)]
                return httpx.Response(200, json={"data": rows, "pagination": {"has_more": True, "next_cursor": "abc", "limit": 2, "offset": 0}})
            if path == "/trades":  # v1 fallback, camelCase, bare array
                cond = q.get("market", "").split(",")[0]
                if q.get("offset", "0") != "0":
                    return httpx.Response(200, json=[])
                return httpx.Response(200, json=[{
                    "proxyWallet": W1, "side": "BUY", "asset": TOK[cond][1], "conditionId": cond, "size": 42.62,
                    "price": 0.91, "timestamp": (NOW - 100) * 1000, "outcome": "No", "outcomeIndex": 1,
                    "transactionHash": "0x" + "9" * 64, "name": "gopfan",
                }])
        if host == "clob.polymarket.com" and path == "/prices-history":
            start = int(q["startTs"])
            return httpx.Response(200, json={"history": [{"t": start + i * 600, "p": 0.2 + 0.001 * i} for i in range(5)]})
        if host == "api.etherscan.io":
            addr = q["address"].lower()
            if addr in (W1, W2):
                rows = [
                    {"hash": "0x" + "a" * 64, "from": FUNDER, "to": addr, "value": "50000000000", "tokenDecimal": "6",
                     "contractAddress": PUSD, "timeStamp": str(NOW - 86400), "logIndex": "3"},
                ]
                return httpx.Response(200, json={"status": "1", "message": "OK", "result": rows})
            if addr == FUNDER:
                rows = [{"hash": "0x" + "b" * 64, "from": "0x" + "e" * 40, "to": FUNDER, "value": "100000000000",
                         "tokenDecimal": "6", "contractAddress": PUSD, "timeStamp": str(NOW - 2 * 86400), "logIndex": "1"}]
                return httpx.Response(200, json={"status": "1", "message": "OK", "result": rows})
            return httpx.Response(200, json={"status": "0", "message": "No transactions found", "result": []})
        return httpx.Response(404, json={"error": f"unmocked {host}{path}"})
