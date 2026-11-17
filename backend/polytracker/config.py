"""Configuration: config.yaml for tunables, environment for secrets.

Scope and alert settings can be overridden at runtime from the dashboard;
those overrides live in the `settings` table and are merged on top of the
YAML values by `effective_config()`.
"""
from __future__ import annotations

import copy
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATHS = [
    os.environ.get("POLYTRACKER_CONFIG", ""),
    "/app/config.yaml",
    str(Path(__file__).resolve().parents[2] / "config.yaml"),
    "config.yaml",
]

# Built-in defaults so the app runs even with a partial config file.
DEFAULTS: dict[str, Any] = {
    "scope": {
        "mode": "tags",
        "tags": ["geopolitics", "politics"],
        "include_keywords": [],
        "exclude_keywords": [],
        "explicit_markets": [],
        "min_volume": 25000,
        "max_markets": 150,
        "include_closed_days": 90,
    },
    "ingestion": {
        "market_sync_minutes": 10,
        "trade_poll_seconds": 15,
        "realtime_ws": True,
        "backfill_max_pages": 40,
        "price_history_fidelity_minutes": 10,
        "min_trade_usd": 5,
    },
    "analysis": {
        "interval_minutes": 10,
        "lookback_days": 180,
        "bet_merge_minutes": 60,
        "co_bet_window_minutes": 30,
        "min_co_bet_markets": 2,
        "max_window_wallets": 25,
        "jump_threshold": 0.20,
        "jump_window_minutes": 120,
        "pre_jump_hours": 12,
        "fresh_wallet_hours": 72,
        "hub_min_fanout": 20,
        "hub_global_recipients": 150,
        "min_edge_weight": 3.0,
        "min_position_usd": 50,
        "keep_runs": 30,
        "weights": {
            "direct_transfer": 12,
            "funding_shared": 10,
            "funding_shared_2hop": 6,
            "withdrawal_shared": 6,
            "co_bet": 1.0,
            "behavior": 3,
        },
    },
    "alerts": {
        "enabled": True,
        "cluster_score": 70,
        "wallet_score": 80,
        "large_trade_usd": 25000,
        "cooldown_hours": 12,
    },
    "onchain": {
        "provider": "etherscan",
        "chain_id": 137,
        "trace_depth": 2,
        "min_wallet_volume_usd": 1000,
        "max_traces_per_cycle": 150,
        "requests_per_second": 4,
        "collateral_tokens": [],
        "system_addresses": ["0x0000000000000000000000000000000000000000"],
        "known_hubs": [],
    },
}

EDITABLE_SECTIONS = ("scope", "alerts")


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


@lru_cache(maxsize=1)
def file_config() -> dict[str, Any]:
    for p in DEFAULT_CONFIG_PATHS:
        if p and Path(p).is_file():
            with open(p, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            cfg = deep_merge(DEFAULTS, data)
            break
    else:
        cfg = copy.deepcopy(DEFAULTS)
    # normalise addresses to lowercase once
    oc = cfg["onchain"]
    for key in ("collateral_tokens", "system_addresses", "known_hubs"):
        oc[key] = [a.lower() for a in oc.get(key) or []]
    return cfg


class Env:
    """Secrets and deployment settings, read from the environment."""

    @property
    def database_url(self) -> str:
        return os.environ.get(
            "DATABASE_URL", "postgresql+psycopg://polytracker:polytracker@localhost:5432/polytracker"
        )

    @property
    def demo_mode(self) -> bool:
        return os.environ.get("DEMO_MODE", "false").lower() in ("1", "true", "yes")

    @property
    def etherscan_api_key(self) -> str:
        return os.environ.get("ETHERSCAN_API_KEY", "")

    @property
    def blockscout_api_key(self) -> str:
        return os.environ.get("BLOCKSCOUT_API_KEY", "")

    @property
    def telegram_bot_token(self) -> str:
        return os.environ.get("TELEGRAM_BOT_TOKEN", "")

    @property
    def telegram_chat_id(self) -> str:
        return os.environ.get("TELEGRAM_CHAT_ID", "")

    @property
    def discord_webhook_url(self) -> str:
        return os.environ.get("DISCORD_WEBHOOK_URL", "")

    @property
    def public_url(self) -> str:
        return os.environ.get("PUBLIC_URL", "http://localhost:8080").rstrip("/")

    @property
    def gamma_url(self) -> str:
        return os.environ.get("POLYMARKET_GAMMA_URL", "https://gamma-api.polymarket.com")

    @property
    def data_url(self) -> str:
        return os.environ.get("POLYMARKET_DATA_URL", "https://data-api.polymarket.com")

    @property
    def clob_url(self) -> str:
        return os.environ.get("POLYMARKET_CLOB_URL", "https://clob.polymarket.com")

    @property
    def rtds_url(self) -> str:
        return os.environ.get("POLYMARKET_RTDS_URL", "wss://ws-live-data.polymarket.com")


env = Env()


def effective_config(overrides: dict | None = None) -> dict[str, Any]:
    """YAML config with dashboard overrides applied (only editable sections)."""
    cfg = file_config()
    if overrides:
        safe = {k: v for k, v in overrides.items() if k in EDITABLE_SECTIONS}
        cfg = deep_merge(cfg, safe)
    return cfg
