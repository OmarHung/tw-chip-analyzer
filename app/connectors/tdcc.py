"""TDCC 集保戶股權分散 connector（openapi 1-5，回傳當週全市場快照）。"""
from __future__ import annotations

import httpx

URL = "https://openapi.tdcc.com.tw/v1/opendata/1-5"
_HEADERS = {"User-Agent": "Mozilla/5.0 (tw-chip-analyzer)"}


async def fetch_shareholder_distribution() -> list[dict]:
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(URL, headers=_HEADERS)
        r.raise_for_status()
        return r.json()
