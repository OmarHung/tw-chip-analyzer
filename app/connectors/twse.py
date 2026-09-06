"""TWSE 盤後資料 connector（只負責抓取 raw JSON）。

端點：
- OHLCV：MI_INDEX（type=ALLBUT0999）
- 三大法人：T86
- 融資融券：MI_MARGN（selectType=STOCK）
"""
from __future__ import annotations

import datetime as dt

import httpx

from app.importers.base import twse_date

BASE = "https://www.twse.com.tw"
_HEADERS = {"User-Agent": "Mozilla/5.0 (tw-chip-analyzer)"}


async def _get(client: httpx.AsyncClient, url: str, params: dict) -> dict:
    r = await client.get(url, params=params, headers=_HEADERS)
    r.raise_for_status()
    return r.json()


async def fetch_ohlcv(date: dt.date) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        return await _get(
            client,
            f"{BASE}/rwd/zh/afterTrading/MI_INDEX",
            {"date": twse_date(date), "type": "ALLBUT0999", "response": "json"},
        )


async def fetch_institutional(date: dt.date) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        return await _get(
            client,
            f"{BASE}/rwd/zh/fund/T86",
            {"date": twse_date(date), "selectType": "ALLBUT0999", "response": "json"},
        )


async def fetch_margin(date: dt.date) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        return await _get(
            client,
            f"{BASE}/rwd/zh/marginTrading/MI_MARGN",
            {"date": twse_date(date), "selectType": "STOCK", "response": "json"},
        )
