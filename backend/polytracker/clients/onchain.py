"""Polygon token-transfer lookups through an Etherscan-compatible API.

Etherscan V2 (default) and Blockscout PRO speak the same `module=account
&action=tokentx` dialect; only the base URL and the chain parameter name differ.
"""
from __future__ import annotations

import logging

from ..config import env
from .http import Http, HttpError
from .normalize import normalize_transfer

log = logging.getLogger(__name__)

PROVIDERS = {
    "etherscan": ("https://api.etherscan.io/v2/api", "chainid"),
    "blockscout": ("https://api.blockscout.com/v2/api", "chain_id"),
}


class ChainClient:
    def __init__(self, http: Http, provider: str = "etherscan", chain_id: int = 137, rps: float = 4):
        if provider not in PROVIDERS:
            raise ValueError(f"unknown on-chain provider {provider!r}")
        self.http = http
        self.base, self.chain_param = PROVIDERS[provider]
        import os

        self.base = os.environ.get("ONCHAIN_API_URL", self.base)
        self.chain_id = chain_id
        self.api_key = env.etherscan_api_key if provider == "etherscan" else env.blockscout_api_key
        from urllib.parse import urlparse

        http.set_rate(urlparse(self.base).netloc, rps)

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def token_transfers(self, address: str, page_size: int = 1000) -> tuple[list[dict], bool]:
        """ERC-20 transfers touching `address`, oldest first.

        Returns (rows, truncated). `truncated` means the provider returned a
        full page, i.e. the address has more history than we fetched, which
        on its own is a strong sign of an exchange or bridge hot wallet.
        """
        params = {
            self.chain_param: self.chain_id,
            "module": "account",
            "action": "tokentx",
            "address": address,
            "page": 1,
            "offset": page_size,
            "sort": "asc",
            "apikey": self.api_key,
        }
        for attempt in range(3):
            data = await self.http.get_json(self.base, params)
            status = str(data.get("status", "0"))
            result = data.get("result")
            if status == "1" and isinstance(result, list):
                rows = [t for t in (normalize_transfer(r) for r in result) if t]
                return rows, len(result) >= page_size
            msg = f"{data.get('message', '')} {result if isinstance(result, str) else ''}".lower()
            if "no transactions found" in msg or (isinstance(result, list) and not result):
                return [], False
            if "rate limit" in msg or "max calls" in msg:
                import asyncio

                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            raise HttpError(200, self.base, f"provider error: {msg.strip()}")
        raise HttpError(429, self.base, "provider rate limit")
