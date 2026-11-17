"""Background worker: one asyncio process running every ingestion and
analysis loop. Each loop reports a heartbeat shown on the Status page."""
from __future__ import annotations

import asyncio
import logging
import signal
import time

from sqlalchemy import func, select, update

from ..analysis.pipeline import run_analysis
from ..clients.http import Http
from ..clients.onchain import ChainClient
from ..clients.polymarket import PolymarketClient
from ..config import effective_config, env
from ..db import config_overrides, get_setting, heartbeat, init_db, insert_ignore, session_scope, upsert
from ..models import AnalysisRun, Market, PricePoint, Trade
from . import alerts
from .store import store_trades
from .tracer import trace_cycle

log = logging.getLogger("polytracker.worker")


def current_config() -> dict:
    with session_scope() as s:
        return effective_config(config_overrides(s))


class Worker:
    def __init__(self, http: Http | None = None):
        self.stop = asyncio.Event()
        self.http = http or Http(default_rps=6)
        self.pm = PolymarketClient(self.http)
        self.tracked: dict[str, list[str]] = {}  # condition_id -> token ids
        self.ws_buffer: list[dict] = []
        self._last_market_sync = 0.0

    # ---------------------------------------------------------- helpers
    def refresh_tracked(self) -> None:
        with session_scope() as s:
            self.tracked = {m.condition_id: m.token_ids or [] for m in s.scalars(select(Market).where(Market.tracked))}

    async def _loop(self, name: str, fn, every_s: float, first_delay: float = 0):
        await asyncio.sleep(first_delay)
        while not self.stop.is_set():
            t0 = time.time()
            try:
                await fn()
                heartbeat(name)
            except Exception as exc:  # keep the loop alive, surface the error
                log.exception("%s failed", name)
                try:
                    heartbeat(name, ok=False, error=f"{exc.__class__.__name__}: {exc}")
                except Exception:
                    pass
            wait = max(1.0, every_s - (time.time() - t0))
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=wait)
            except asyncio.TimeoutError:
                pass

    async def _ingest(self, rows: list[dict], cfg: dict) -> None:
        new = await asyncio.to_thread(store_trades, rows, float(cfg["ingestion"]["min_trade_usd"]))
        if new:
            heartbeat("trades", n_trades=len(new))
            created = await asyncio.to_thread(alerts.evaluate_trades, new, cfg)
            await alerts.deliver(created)

    # ------------------------------------------------------------ markets
    async def market_sync(self) -> None:
        """Checked every 30 s; resyncs when due or when Settings changed the scope."""
        cfg = current_config()
        with session_scope() as s:
            requested = get_setting(s, "market_sync_requested_at", 0) or 0
        due = time.time() - self._last_market_sync >= cfg["ingestion"]["market_sync_minutes"] * 60
        if not (due or requested > self._last_market_sync):
            return
        self._last_market_sync = time.time()
        rows = await self.pm.scoped_markets(cfg["scope"])
        now = int(time.time())
        ids = [r["condition_id"] for r in rows]
        with session_scope() as s:
            for r in rows:
                r["tracked"] = True
                r["updated_at"] = now
            upsert(s, Market, rows, ["condition_id"],
                   [c for c in rows[0].keys() if c not in ("condition_id",)] if rows else None)
            if ids:
                s.execute(update(Market).where(Market.condition_id.not_in(ids)).values(tracked=False))
        self.refresh_tracked()
        heartbeat("markets", tracked=len(ids))
        log.info("market sync: %d markets in scope", len(ids))

    # ------------------------------------------------------------- trades
    async def backfill(self) -> None:
        cfg = current_config()
        ing = cfg["ingestion"]
        with session_scope() as s:
            todo = s.scalars(
                select(Market.condition_id).where(Market.tracked, Market.backfilled_at.is_(None))
                .order_by(Market.volume.desc()).limit(5)
            ).all()
        for cid in todo:
            n = 0
            async for page in self.pm.iter_trades(
                conditions=[cid], max_pages=int(ing["backfill_max_pages"]), source="backfill",
                taker_only=False,
            ):
                new = await asyncio.to_thread(store_trades, page, float(ing["min_trade_usd"]))
                n += len(new)
            with session_scope() as s:
                m = s.get(Market, cid)
                if m:
                    m.backfilled_at = int(time.time())
            heartbeat("backfill", n_trades=n, n_markets=1)
            log.info("backfilled %s: %d trades", cid[:12], n)

    async def poll(self) -> None:
        cfg = current_config()
        ids = list(self.tracked)
        if not ids:
            return
        with session_scope() as s:
            last = s.scalar(select(func.max(Trade.ts)).where(Trade.source != "backfill")) or 0
        stop_before = max(last - 600, int(time.time()) - 6 * 3600)
        for i in range(0, len(ids), 20):
            batch = ids[i : i + 20]
            async for page in self.pm.iter_trades(
                conditions=batch, max_pages=3, page_size=500, stop_before_ts=stop_before, taker_only=False
            ):
                await self._ingest(page, cfg)

    async def websocket(self) -> None:
        cfg = current_config()

        async def on_trade(t: dict) -> None:
            if t["condition_id"] in self.tracked:
                self.ws_buffer.append(t)

        async def flusher():
            while not self.stop.is_set():
                await asyncio.sleep(2)
                if self.ws_buffer:
                    rows, self.ws_buffer = self.ws_buffer, []
                    await self._ingest(rows, cfg)
                    heartbeat("websocket", n_messages=len(rows))

        f = asyncio.create_task(flusher())
        try:
            await self.pm.stream_trades(on_trade, self.stop)
        finally:
            f.cancel()

    # ------------------------------------------------------------- prices
    async def prices(self) -> None:
        cfg = current_config()
        fid = int(cfg["ingestion"]["price_history_fidelity_minutes"])
        lookback = int(cfg["analysis"]["lookback_days"] * 86400)
        now = int(time.time())
        with session_scope() as s:
            ms = s.scalars(select(Market).where(Market.tracked)).all()
            todo = [(m.condition_id, (m.token_ids or [None])[0], m.prices_synced_at, m.closed_ts) for m in ms]
        for cid, token, synced, closed_ts in todo:
            if not token:
                continue
            if closed_ts and synced and synced > closed_ts:
                continue  # closed market already fully synced
            start = (synced - 3600) if synced else now - lookback
            pts = await self.pm.price_history(token, start, now, fid)
            with session_scope() as s:
                insert_ignore(s, PricePoint, [{"token_id": token, "ts": t, "price": p} for t, p in pts],
                              ["token_id", "ts"])
                m = s.get(Market, cid)
                if m:
                    m.prices_synced_at = now
            heartbeat("prices", n_points=len(pts))

    # ------------------------------------------------------------- onchain
    async def tracer(self, chain: ChainClient) -> None:
        cfg = current_config()
        stats = await trace_cycle(chain, cfg)
        if stats.get("n_wallets_traced"):
            log.info("tracer: %s", stats)

    # ------------------------------------------------------------ analysis
    async def analysis_scheduler(self) -> None:
        """Runs analysis on schedule, or immediately when the dashboard asks."""
        last_run = 0.0
        while not self.stop.is_set():
            cfg = current_config()
            every = float(cfg["analysis"]["interval_minutes"]) * 60
            with session_scope() as s:
                req = get_setting(s, "analysis_requested_at", 0) or 0
                has_trades = s.scalar(select(func.count()).select_from(Trade)) or 0
            due = time.time() - last_run >= every
            requested = req > last_run
            if has_trades and (due or requested):
                last_run = time.time()
                try:
                    summary = await asyncio.to_thread(run_analysis, cfg)
                    heartbeat("analysis", run_id=summary["run_id"], duration_s=summary["duration_s"])
                    created = await asyncio.to_thread(alerts.evaluate_run, summary["run_id"], cfg)
                    await alerts.deliver(created)
                except Exception as exc:
                    log.exception("analysis failed")
                    heartbeat("analysis", ok=False, error=repr(exc))
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass

    # ---------------------------------------------------------------- demo
    async def demo_ticker(self) -> None:
        from ..demo.seed import DemoTicker

        ticker = DemoTicker()
        cfg = current_config()
        while not self.stop.is_set():
            rows = await asyncio.to_thread(ticker.tick)
            await self._ingest(rows, cfg)
            heartbeat("demo", n_trades=len(rows))
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=4)
            except asyncio.TimeoutError:
                pass

    # ----------------------------------------------------------------- run
    async def run(self) -> None:
        init_db()
        cfg = current_config()
        ing = cfg["ingestion"]
        tasks = []
        if env.demo_mode:
            with session_scope() as s:
                n = s.scalar(select(func.count()).select_from(Market)) or 0
            if n == 0:
                from ..demo.seed import seed_demo

                log.info("DEMO_MODE: seeding synthetic dataset")
                await asyncio.to_thread(seed_demo)
            log.info("DEMO_MODE: no network calls; simulating a live feed")
            tasks += [self.analysis_scheduler(), self.demo_ticker()]
        else:
            self.refresh_tracked()
            oc = cfg["onchain"]
            chain = ChainClient(self.http, oc["provider"], int(oc["chain_id"]), float(oc["requests_per_second"]))
            tasks += [
                self._loop("markets", self.market_sync, 30),
                self._loop("backfill", self.backfill, 60, first_delay=20),
                self._loop("poll", self.poll, ing["trade_poll_seconds"], first_delay=30),
                self._loop("prices", self.prices, 30 * 60, first_delay=60),
                self.analysis_scheduler(),
            ]
            if ing.get("realtime_ws", True):
                tasks.append(self._loop("websocket", self.websocket, 5, first_delay=15))
            if chain.enabled:
                tasks.append(self._loop("tracer", lambda: self.tracer(chain), 120, first_delay=90))
            else:
                log.warning("No ETHERSCAN_API_KEY/BLOCKSCOUT_API_KEY: on-chain tracing disabled "
                            "(funding/withdrawal links and wallet age will be missing)")
                heartbeat("tracer", ok=False, error="disabled: no API key configured")
        await asyncio.gather(*tasks)

    def shutdown(self, *_):
        log.info("shutting down")
        self.stop.set()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    w = Worker()
    loop = asyncio.new_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, w.shutdown)
        except NotImplementedError:  # pragma: no cover (windows)
            pass
    try:
        loop.run_until_complete(w.run())
    finally:
        loop.run_until_complete(w.http.aclose())
        loop.close()
