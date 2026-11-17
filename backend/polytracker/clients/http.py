"""Shared async HTTP client: retries, Retry-After, per-host rate limiting."""
from __future__ import annotations

import asyncio
import logging
import random
import time
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)

USER_AGENT = "PolyTracker/2.0 (open-data research; +https://github.com/)"


class RateLimiter:
    """Simple per-host spacing limiter (requests per second)."""

    def __init__(self, rps: float):
        self.min_interval = 1.0 / rps if rps > 0 else 0
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        if not self.min_interval:
            return
        async with self._lock:
            now = time.monotonic()
            delay = self._next - now
            if delay > 0:
                await asyncio.sleep(delay)
            self._next = max(now, self._next) + self.min_interval


class HttpError(Exception):
    def __init__(self, status: int, url: str, body: str = ""):
        super().__init__(f"HTTP {status} on {url}: {body[:300]}")
        self.status = status
        self.url = url
        self.body = body


class Http:
    def __init__(self, default_rps: float = 5.0, timeout: float = 30.0, max_retries: int = 5,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.client = httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            follow_redirects=True,
            transport=transport,
        )
        self.default_rps = default_rps
        self.max_retries = max_retries
        self._limiters: dict[str, RateLimiter] = {}

    def set_rate(self, host: str, rps: float) -> None:
        self._limiters[host] = RateLimiter(rps)

    def _limiter(self, url: str) -> RateLimiter:
        host = urlparse(url).netloc
        if host not in self._limiters:
            self._limiters[host] = RateLimiter(self.default_rps)
        return self._limiters[host]

    async def get_json(self, url: str, params: dict | None = None, headers: dict | None = None):
        params = {k: v for k, v in (params or {}).items() if v is not None}
        for attempt in range(self.max_retries + 1):
            await self._limiter(url).wait()
            try:
                r = await self.client.get(url, params=params, headers=headers)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                if attempt == self.max_retries:
                    raise
                await asyncio.sleep(min(2**attempt, 30) + random.random())
                log.debug("transport error %s on %s, retrying", exc.__class__.__name__, url)
                continue
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                retry_after = r.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after else min(2**attempt, 30)
                except ValueError:
                    delay = min(2**attempt, 30)
                log.info("HTTP %s on %s, retrying in %.1fs", r.status_code, url, delay)
                await asyncio.sleep(delay + random.random() * 0.5)
                continue
            raise HttpError(r.status_code, str(r.url), r.text)
        raise HttpError(0, url, "retries exhausted")

    async def post_json(self, url: str, payload: dict):
        await self._limiter(url).wait()
        r = await self.client.post(url, json=payload)
        if r.status_code >= 300:
            raise HttpError(r.status_code, url, r.text)
        return r.json() if r.content else None

    async def aclose(self) -> None:
        await self.client.aclose()
