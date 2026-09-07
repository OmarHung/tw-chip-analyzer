"""TPEx 上櫃盤後資料 connector（只負責抓取 raw JSON）。

端點（新版官網 www JSON,日期為民國 yyy/mm/dd）:
- 行情:afterTrading/dailyQuotes(type=EW 上櫃一般板)
- 三大法人:insti/dailyTrade(type=Daily, sect=EW)
- 融資融券:margin/balance

注意:TPEx 憑證缺 Subject Key Identifier 擴展,Python 3.13+ 預設 strict
X509 驗證會拒絕;此處保留一般憑證驗證、僅關閉 strict 旗標。
"""
from __future__ import annotations

import datetime as dt
import ssl

import httpx

BASE = "https://www.tpex.org.tw/www/zh-tw"
_HEADERS = {"User-Agent": "Mozilla/5.0 (tw-chip-analyzer)"}

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT


def roc_date(d: dt.date) -> str:
    """date → 民國 'yyy/mm/dd'(TPEx 參數格式)。"""
    return f"{d.year - 1911}/{d.month:02d}/{d.day:02d}"


async def _get(url: str, params: dict) -> dict:
    async with httpx.AsyncClient(timeout=30, verify=_ssl_ctx) as client:
        r = await client.get(url, params=params, headers=_HEADERS)
        r.raise_for_status()
        return r.json()


async def fetch_ohlcv(date: dt.date) -> dict:
    return await _get(
        f"{BASE}/afterTrading/dailyQuotes",
        {"date": roc_date(date), "type": "EW", "response": "json"},
    )


async def fetch_institutional(date: dt.date) -> dict:
    return await _get(
        f"{BASE}/insti/dailyTrade",
        {"date": roc_date(date), "type": "Daily", "sect": "EW", "response": "json"},
    )


async def fetch_margin(date: dt.date) -> dict:
    return await _get(
        f"{BASE}/margin/balance",
        {"date": roc_date(date), "response": "json"},
    )
