"""Database models. Timestamps are epoch seconds (UTC) throughout."""
from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JSONType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------- markets
class Market(Base):
    __tablename__ = "markets"

    condition_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    gamma_id: Mapped[str | None] = mapped_column(String(32))
    slug: Mapped[str | None] = mapped_column(String(300), index=True)
    question: Mapped[str] = mapped_column(Text, default="")
    event_slug: Mapped[str | None] = mapped_column(String(300))
    event_title: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSONType, default=list)
    outcomes: Mapped[list] = mapped_column(JSONType, default=list)
    token_ids: Mapped[list] = mapped_column(JSONType, default=list)
    volume: Mapped[float] = mapped_column(Float, default=0.0)
    liquidity: Mapped[float] = mapped_column(Float, default=0.0)
    last_price: Mapped[float | None] = mapped_column(Float)  # price of outcome 0
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    closed: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    winning_index: Mapped[int | None] = mapped_column(Integer)
    end_ts: Mapped[int | None] = mapped_column(BigInteger)
    closed_ts: Mapped[int | None] = mapped_column(BigInteger)
    tracked: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    backfilled_at: Mapped[int | None] = mapped_column(BigInteger)
    prices_synced_at: Mapped[int | None] = mapped_column(BigInteger)
    updated_at: Mapped[int] = mapped_column(BigInteger, default=0)


# ---------------------------------------------------------------------- trades
class Trade(Base):
    __tablename__ = "trades"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uid: Mapped[str] = mapped_column(String(160), unique=True)
    wallet: Mapped[str] = mapped_column(String(42), index=True)
    condition_id: Mapped[str] = mapped_column(String(80), index=True)
    token_id: Mapped[str | None] = mapped_column(String(100))
    outcome: Mapped[str | None] = mapped_column(String(120))
    outcome_index: Mapped[int] = mapped_column(Integer, default=0)
    side: Mapped[str] = mapped_column(String(4))  # BUY | SELL
    price: Mapped[float] = mapped_column(Float)
    size: Mapped[float] = mapped_column(Float)  # shares
    usd: Mapped[float] = mapped_column(Float)
    ts: Mapped[int] = mapped_column(BigInteger, index=True)
    tx_hash: Mapped[str | None] = mapped_column(String(80))
    source: Mapped[str] = mapped_column(String(12), default="poll")

    __table_args__ = (Index("ix_trades_market_ts", "condition_id", "ts"),)


# --------------------------------------------------------------------- wallets
class Wallet(Base):
    __tablename__ = "wallets"

    address: Mapped[str] = mapped_column(String(42), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(200))
    pseudonym: Mapped[str | None] = mapped_column(String(200))
    first_trade_ts: Mapped[int | None] = mapped_column(BigInteger)
    last_trade_ts: Mapped[int | None] = mapped_column(BigInteger)
    first_funded_ts: Mapped[int | None] = mapped_column(BigInteger)
    traced_at: Mapped[int | None] = mapped_column(BigInteger)
    trace_status: Mapped[str | None] = mapped_column(String(40))


class Address(Base):
    """Any on-chain address met while tracing (funders, destinations)."""

    __tablename__ = "addresses"

    address: Mapped[str] = mapped_column(String(42), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), default="external")  # external|hub|system
    n_counterparties: Mapped[int] = mapped_column(Integer, default=0)
    label: Mapped[str | None] = mapped_column(String(200))
    traced_at: Mapped[int | None] = mapped_column(BigInteger)


class Transfer(Base):
    __tablename__ = "transfers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uid: Mapped[str] = mapped_column(String(220), unique=True)
    tx_hash: Mapped[str] = mapped_column(String(80))
    token: Mapped[str] = mapped_column(String(42))
    from_addr: Mapped[str] = mapped_column(String(42), index=True)
    to_addr: Mapped[str] = mapped_column(String(42), index=True)
    amount: Mapped[float] = mapped_column(Float)
    ts: Mapped[int] = mapped_column(BigInteger)


# -------------------------------------------------------------------- prices
class PricePoint(Base):
    __tablename__ = "price_points"

    token_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    ts: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    price: Mapped[float] = mapped_column(Float)


class PriceJump(Base):
    __tablename__ = "price_jumps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    condition_id: Mapped[str] = mapped_column(String(80), index=True)
    start_ts: Mapped[int] = mapped_column(BigInteger)
    end_ts: Mapped[int] = mapped_column(BigInteger)
    p_before: Mapped[float] = mapped_column(Float)
    p_after: Mapped[float] = mapped_column(Float)
    direction: Mapped[int] = mapped_column(Integer)  # +1 outcome-0 up, -1 down


# ------------------------------------------------------------------ analysis
class AnalysisRun(Base):
    __tablename__ = "analysis_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[int] = mapped_column(BigInteger)
    finished_at: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16), default="running")
    stats: Mapped[dict] = mapped_column(JSONType, default=dict)
    error: Mapped[str | None] = mapped_column(Text)


class WalletScore(Base):
    __tablename__ = "wallet_scores"

    run_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), primary_key=True
    )
    wallet: Mapped[str] = mapped_column(String(42), primary_key=True)
    score: Mapped[float] = mapped_column(Float, index=True)
    cluster_key: Mapped[str | None] = mapped_column(String(16), index=True)
    components: Mapped[list] = mapped_column(JSONType, default=list)
    stats: Mapped[dict] = mapped_column(JSONType, default=dict)


class Cluster(Base):
    __tablename__ = "clusters"

    run_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String(16), primary_key=True)
    score: Mapped[float] = mapped_column(Float)
    size: Mapped[int] = mapped_column(Integer)
    members: Mapped[list] = mapped_column(JSONType, default=list)
    components: Mapped[list] = mapped_column(JSONType, default=list)
    stats: Mapped[dict] = mapped_column(JSONType, default=dict)
    top_markets: Mapped[list] = mapped_column(JSONType, default=list)


class Edge(Base):
    __tablename__ = "edges"

    run_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), primary_key=True
    )
    a: Mapped[str] = mapped_column(String(42), primary_key=True)
    b: Mapped[str] = mapped_column(String(42), primary_key=True)
    weight: Mapped[float] = mapped_column(Float)
    kinds: Mapped[list] = mapped_column(JSONType, default=list)
    evidence: Mapped[list] = mapped_column(JSONType, default=list)


# --------------------------------------------------------------- operations
class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[int] = mapped_column(BigInteger, index=True)
    kind: Mapped[str] = mapped_column(String(20))  # cluster | wallet | trade
    ref: Mapped[str] = mapped_column(String(120))
    score: Mapped[float] = mapped_column(Float, default=0)
    title: Mapped[str] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    dedupe_key: Mapped[str] = mapped_column(String(160), index=True)
    channels: Mapped[list] = mapped_column(JSONType, default=list)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONType, default=dict)
    updated_at: Mapped[int] = mapped_column(BigInteger, default=0)


class WorkerStatus(Base):
    __tablename__ = "worker_status"

    task: Mapped[str] = mapped_column(String(40), primary_key=True)
    heartbeat: Mapped[int] = mapped_column(BigInteger, default=0)
    last_ok: Mapped[int | None] = mapped_column(BigInteger)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_error_at: Mapped[int | None] = mapped_column(BigInteger)
    counters: Mapped[dict] = mapped_column(JSONType, default=dict)

    __table_args__ = (UniqueConstraint("task"),)
