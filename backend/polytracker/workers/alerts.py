"""Alerts: decide what deserves a notification, dedupe, and deliver."""
from __future__ import annotations

import html
import logging
import time

import httpx
from sqlalchemy import desc, select

from ..config import env
from ..db import session_scope
from ..models import Alert, AnalysisRun, Cluster, Market, WalletScore

log = logging.getLogger(__name__)


def _short(a: str) -> str:
    return f"{a[:6]}…{a[-4:]}"


def _usd(x: float) -> str:
    return f"${x / 1e6:.2f}M" if x >= 1e6 else (f"${x / 1e3:.1f}k" if x >= 1e3 else f"${x:.0f}")


def _recent(s, dedupe_key: str, cooldown_s: int) -> Alert | None:
    return s.scalars(
        select(Alert)
        .where(Alert.dedupe_key == dedupe_key, Alert.created_at >= int(time.time()) - cooldown_s)
        .order_by(desc(Alert.created_at))
    ).first()


def evaluate_run(run_id: int, cfg: dict) -> list[dict]:
    """Create alerts for clusters/wallets that crossed thresholds in this run."""
    al = cfg["alerts"]
    if not al.get("enabled", True):
        return []
    cooldown = int(al["cooldown_hours"] * 3600)
    objs: list[Alert] = []
    with session_scope() as s:
        clusters = s.scalars(
            select(Cluster).where(Cluster.run_id == run_id, Cluster.score >= al["cluster_score"] - 0.5)
        ).all()
        alerted_members: set[str] = set()
        for c in clusters:
            key = f"cluster:{c.key}"
            prev = _recent(s, key, cooldown)
            if prev and c.score < prev.score + 10:
                alerted_members |= set(c.members)
                continue
            top = c.top_markets[0]["question"] if c.top_markets else "—"
            reasons = "; ".join(x["detail"] for x in sorted(c.components, key=lambda x: -x["points"])[:2])
            a = Alert(
                created_at=int(time.time()), kind="cluster", ref=c.key, score=c.score,
                title=f"Cluster of {c.size} wallets scores {c.score:.0f}/100",
                message=f"Top market: {top}. {reasons}. Volume {_usd(c.stats.get('volume', 0))}.",
                dedupe_key=key, channels=[],
            )
            s.add(a)
            alerted_members |= set(c.members)
            objs.append(a)
        wallets = s.scalars(
            select(WalletScore).where(WalletScore.run_id == run_id, WalletScore.score >= al["wallet_score"] - 0.5)
        ).all()
        for w in wallets:
            if w.wallet in alerted_members:
                continue
            key = f"wallet:{w.wallet}"
            prev = _recent(s, key, cooldown)
            if prev and w.score < prev.score + 10:
                continue
            reasons = "; ".join(x["detail"] for x in sorted(w.components, key=lambda x: -x["points"])[:2])
            a = Alert(
                created_at=int(time.time()), kind="wallet", ref=w.wallet, score=w.score,
                title=f"Wallet {_short(w.wallet)} scores {w.score:.0f}/100",
                message=reasons, dedupe_key=key, channels=[],
            )
            s.add(a)
            objs.append(a)
        s.flush()
        created = [_as_dict(a) for a in objs]
    return created


def evaluate_trades(trades: list[dict], cfg: dict) -> list[dict]:
    """Live alert: a high-scoring wallet just placed a large trade."""
    al = cfg["alerts"]
    if not al.get("enabled", True):
        return []
    big = [t for t in trades if t["usd"] >= al["large_trade_usd"] and t.get("source") != "backfill"]
    if not big:
        return []
    created = []
    with session_scope() as s:
        run = s.scalars(select(AnalysisRun).where(AnalysisRun.status == "done").order_by(desc(AnalysisRun.id))).first()
        if not run:
            return []
        scores = {
            w.wallet: w
            for w in s.scalars(
                select(WalletScore).where(
                    WalletScore.run_id == run.id, WalletScore.wallet.in_([t["wallet"] for t in big])
                )
            )
        }
        for t in big:
            ws = scores.get(t["wallet"])
            if not ws or round(ws.score) < al["wallet_score"]:
                continue
            m = s.get(Market, t["condition_id"])
            q = m.question if m else t["condition_id"][:12]
            key = f"trade:{t['uid']}"
            if _recent(s, key, 86400):
                continue
            a = Alert(
                created_at=int(time.time()), kind="trade", ref=t["wallet"], score=ws.score,
                title=f"{_short(t['wallet'])} (score {ws.score:.0f}) just bet {_usd(t['usd'])}",
                message=f"{t['side']} {t.get('outcome') or ''} at {t['price'] * 100:.0f}¢ on “{q}”",
                dedupe_key=key, channels=[],
            )
            s.add(a)
            s.flush()
            d = _as_dict(a)
            d["id"] = a.id
            created.append(d)
    return created


def _as_dict(a: Alert) -> dict:
    return {"id": a.id, "kind": a.kind, "ref": a.ref, "score": a.score, "title": a.title,
            "message": a.message, "created_at": a.created_at}


def _link(a: dict) -> str:
    base = env.public_url
    if a["kind"] == "cluster":
        return f"{base}/clusters/{a['ref']}"
    return f"{base}/wallets/{a['ref']}"


async def deliver(alerts: list[dict]) -> None:
    if not alerts:
        return
    tg = env.telegram_bot_token and env.telegram_chat_id
    dc = env.discord_webhook_url
    if not (tg or dc):
        return
    async with httpx.AsyncClient(timeout=15) as client:
        for a in alerts:
            sent = []
            if tg:
                text = (f"<b>{html.escape(a['title'])}</b>\n{html.escape(a['message'])}\n"
                        f"<a href=\"{_link(a)}\">Open in PolyTracker</a>")
                try:
                    r = await client.post(
                        f"https://api.telegram.org/bot{env.telegram_bot_token}/sendMessage",
                        json={"chat_id": env.telegram_chat_id, "text": text, "parse_mode": "HTML",
                              "disable_web_page_preview": True},
                    )
                    if r.status_code < 300:
                        sent.append("telegram")
                    else:
                        log.warning("telegram: %s %s", r.status_code, r.text[:200])
                except httpx.HTTPError as e:
                    log.warning("telegram: %s", e)
            if dc:
                color = 0xFF5C5C if a["score"] >= 70 else 0xFFB224
                try:
                    r = await client.post(dc, json={"embeds": [{
                        "title": a["title"][:250], "description": a["message"][:3500],
                        "url": _link(a), "color": color,
                    }]})
                    if r.status_code < 300:
                        sent.append("discord")
                    else:
                        log.warning("discord: %s %s", r.status_code, r.text[:200])
                except httpx.HTTPError as e:
                    log.warning("discord: %s", e)
            if sent and a.get("id"):
                with session_scope() as s:
                    row = s.get(Alert, a["id"])
                    if row:
                        row.channels = sent
