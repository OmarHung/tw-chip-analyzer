"""TPEx 上櫃盤後資料 connector（只負責抓取 raw JSON）。

端點（新版官網 www JSON,日期為民國 yyy/mm/dd）:
- 行情:afterTrading/dailyQuotes(type=EW 上櫃一般板)
- 三大法人:insti/dailyTrade(type=Daily, sect=EW)
- 融資融券:margin/balance

公司行動（bulletin/*，**POST form-urlencoded**、可查歷史區間）:
- 除權除息計算結果:bulletin/exDailyQ —— startDate/endDate 為**民國** 115/06/01
- 面額變更恢復參考價:bulletin/pvChgRslt —— startDate/endDate 為**西元** 2026/06/01
- 減資恢復交易參考價:bulletin/revivt —— startDate/endDate 為**西元** 2026/06/01
三者日期格式不一致(實測),傳錯會回 stat="參數錯誤"。

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


_POST_HEADERS = {
    **_HEADERS,
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Referer": f"{BASE}/",
}


async def _post_range(action: str, start: str, end: str) -> dict:
    """bulletin/* 公司行動端點:POST form-urlencoded,startDate/endDate 區間查詢。"""
    body = f"startDate={start}&endDate={end}&id=&response=json"
    async with httpx.AsyncClient(timeout=30, verify=_ssl_ctx) as client:
        r = await client.post(
            f"{BASE}/bulletin/{action}", content=body, headers=_POST_HEADERS
        )
        r.raise_for_status()
        return r.json()


def _ad_date(d: dt.date) -> str:
    """date → 西元 'YYYY/mm/dd'(pvChgRslt / revivt 用)。"""
    return f"{d.year}/{d.month:02d}/{d.day:02d}"


async def fetch_ex_dividend(start: dt.date, end: dt.date | None = None) -> dict:
    """除權除息計算結果表(exDailyQ)。日期為**民國**;含「每仟股無償配股」→ 量因子。"""
    end = end or start
    return await _post_range("exDailyQ", roc_date(start), roc_date(end))


async def fetch_par_change(start: dt.date, end: dt.date | None = None) -> dict:
    """變更股票面額恢復買賣參考價(pvChgRslt)。日期為**西元**。"""
    end = end or start
    return await _post_range("pvChgRslt", _ad_date(start), _ad_date(end))


async def fetch_capital_reduction(start: dt.date, end: dt.date | None = None) -> dict:
    """減資恢復交易參考價(revivt)。日期為**西元**。"""
    end = end or start
    return await _post_range("revivt", _ad_date(start), _ad_date(end))


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
