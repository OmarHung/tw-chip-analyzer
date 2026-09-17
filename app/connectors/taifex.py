"""TAIFEX 期交所盤後資料 connector（只負責抓取 raw CSV 文字）。

端點：
- 每日行情下載 futDataDown（down_type=1）：指定商品 + 日期區間的每日交易行情 CSV。
  欄位含 開高低收/漲跌價/漲跌%/成交量/結算價/未沖銷契約數/交易時段。

兩個必知的細節：
- 回應是 **Big5(MS950)** 編碼的 CSV，不是 JSON；httpx 依 header 猜不準，一律自行 decode。
- 同一交易日期同一契約月份會出現兩列：「一般」(日盤) 與「盤後」(夜盤)。夜盤跨到隔日
  凌晨才收，EOD 當下抓到的是半截資料，故本專案只取一般時段（見 importers/taifex.py）。
"""
from __future__ import annotations

import datetime as dt

import httpx

BASE = "https://www.taifex.com.tw"
_HEADERS = {"User-Agent": "Mozilla/5.0 (tw-chip-analyzer)"}

# 臺股期貨（大台）。小台為 MTX、電子 TE、金融 TF——目前只用大台。
COMMODITY_TX = "TX"


def taifex_date(d: dt.date) -> str:
    return d.strftime("%Y/%m/%d")


async def fetch_futures_daily(
    start: dt.date, end: dt.date | None = None, commodity: str = COMMODITY_TX
) -> str:
    """期貨每日交易行情 CSV（Big5 解碼後的文字）。

    日期區間可跨多日，歷史可回補（與 TWT48U/TWTAVU 那種「只回未來」的預告表不同）。
    """
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{BASE}/cht/3/futDataDown",
            data={
                "down_type": "1",
                "commodity_id": commodity,
                "queryStartDate": taifex_date(start),
                "queryEndDate": taifex_date(end or start),
            },
            headers=_HEADERS,
        )
        r.raise_for_status()
        return r.content.decode("ms950", errors="replace")
