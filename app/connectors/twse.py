"""TWSE 盤後資料 connector（只負責抓取 raw JSON）。

端點：
- OHLCV：MI_INDEX（type=ALLBUT0999）
- 三大法人：T86
- 融資融券：MI_MARGN（selectType=STOCK）
- 借券 SBL：TWT93U（信用額度總量管制餘額表，含融券段 + 借券段）
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


async def fetch_sbl(date: dt.date) -> dict:
    """TWT93U 信用額度總量管制餘額表（借券 SBL 每檔餘額/賣出/還券）。"""
    async with httpx.AsyncClient(timeout=30) as client:
        return await _get(
            client,
            f"{BASE}/rwd/zh/marginTrading/TWT93U",
            {"date": twse_date(date), "response": "json"},
        )


async def fetch_ex_dividend(start: dt.date, end: dt.date | None = None) -> dict:
    """TWT49U 除權除息計算結果表（含除權息前收盤價/參考價/權值息值）。

    以 startDate/endDate 區間查詢（date 參數無效，會被忽略）；單日則 end=start。
    與 SBL 不同，此報表歷史區間可查，故可回補。
    """
    end = end or start
    async with httpx.AsyncClient(timeout=30) as client:
        return await _get(
            client,
            f"{BASE}/exchangeReport/TWT49U",
            {"startDate": twse_date(start), "endDate": twse_date(end),
             "response": "json"},
        )


async def fetch_ex_rights_forecast(
    start: dt.date | None = None, end: dt.date | None = None
) -> dict:
    """TWT48U 除權除息預告表（含無償配股率／現金增資配股率／現金股利）。

    唯一能取得「配股率」的來源——TWT49U 各欄位都無法分離出它（見 parse_ex_dividend）。
    代價是**只回未來尚未執行的事件**，start/end 參數無效（傳了也回同一份），故歷史補不
    回來，只能靠每日 EOD 往前累積（與 SBL 同性質）。參數保留只為與其他 fetch 同簽名。
    """
    async with httpx.AsyncClient(timeout=30) as client:
        return await _get(
            client, f"{BASE}/exchangeReport/TWT48U", {"response": "json"}
        )


async def fetch_par_change(start: dt.date, end: dt.date | None = None) -> dict:
    """TWTB8U 變更股票面額恢復買賣參考價格（拆股/面額變更；TWT49U 不含此類）。

    欄位同 TWTAUU：停止買賣前收盤價格 / 恢復買賣參考價 → adj_factor。歷史區間可查。
    """
    end = end or start
    async with httpx.AsyncClient(timeout=30) as client:
        return await _get(
            client,
            f"{BASE}/rwd/zh/change/TWTB8U",
            {"startDate": twse_date(start), "endDate": twse_date(end),
             "response": "json"},
        )


async def fetch_capital_reduction(start: dt.date, end: dt.date | None = None) -> dict:
    """TWTAUU 股票減資恢復買賣參考價格（減資；價漲、factor>1）。歷史區間可查。"""
    end = end or start
    async with httpx.AsyncClient(timeout=30) as client:
        return await _get(
            client,
            f"{BASE}/rwd/zh/reducation/TWTAUU",
            {"startDate": twse_date(start), "endDate": twse_date(end),
             "response": "json"},
        )


async def fetch_capital_reduction_forecast(
    start: dt.date | None = None, end: dt.date | None = None
) -> dict:
    """TWTAVU 減資預告表（含「減資換股率」＝減資後發行股數/減資前發行股數）。

    現金減資（退還股款）的量因子唯一正確來源：TWTAUU 的參考價已扣掉每股退還股款，
    1/adj_factor 會高估留存股數（見 parse_resume_reference）。與 TWT48U 同性質——
    **只回尚未執行的事件**，stkNo/date 參數無效（傳了也回同一份），故歷史補不回來，
    只能靠每日 EOD 往前累積。參數保留只為與其他 fetch 同簽名。
    """
    async with httpx.AsyncClient(timeout=30) as client:
        return await _get(
            client, f"{BASE}/rwd/zh/reducation/TWTAVU", {"response": "json"}
        )


async def fetch_index_month(date: dt.date) -> dict:
    """FMTQIK：回傳該月每日大盤成交與 TAIEX 收盤指數。"""
    async with httpx.AsyncClient(timeout=30) as client:
        return await _get(
            client,
            f"{BASE}/rwd/zh/afterTrading/FMTQIK",
            {"date": twse_date(date), "response": "json"},
        )
