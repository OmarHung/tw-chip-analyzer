"""TDCC 集保戶股權分散 connector。

- `fetch_shareholder_distribution`：openapi 1-5，當週全市場快照（每週 EOD 用）。
- `TdccWebClient`：個股查詢頁，可回溯約 51 週（歷史回補用，逐檔逐週）。
"""
from __future__ import annotations

import re

import httpx

URL = "https://openapi.tdcc.com.tw/v1/opendata/1-5"
_HEADERS = {"User-Agent": "Mozilla/5.0 (tw-chip-analyzer)"}


async def fetch_shareholder_distribution() -> list[dict]:
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(URL, headers=_HEADERS)
        r.raise_for_status()
        return r.json()


_WEB_URL = "https://www.tdcc.com.tw/portal/zh/smWeb/qryStock"


class TdccWebClient:
    """集保個股股權分散查詢頁（qryStock）——**歷史週次的唯一免費來源**。

    openapi 1-5 只回當週快照、無日期參數；FinMind 的對應資料集需付費層。此查詢頁
    的下拉可回溯約 51 週，但只能**逐檔逐週**查（一次一檔一週，回應約 60KB），
    故僅適合針對重點標的回補，全市場 51 週約需 15 萬次請求，不建議。

    需先 GET 取得 SYNCHRONIZER_TOKEN（CSRF）與 session cookie，之後 POST 查詢。
    **token 是一次性的**：用過即失效，後續 POST 會回到沒有資料表的空頁（實測第一
    次 16 列、之後全 0）。故每次 POST 後都從回應頁重新取出新 token；取不到則重新
    GET 一次換發，避免整批回補只有第一筆有資料。
    """

    def __init__(self, timeout: float = 30.0):
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._token: str | None = None
        self.available_dates: list[str] = []

    async def __aenter__(self) -> "TdccWebClient":
        self._client = httpx.AsyncClient(
            timeout=self._timeout, headers=_HEADERS, follow_redirects=True
        )
        r = await self._client.get(_WEB_URL)
        r.raise_for_status()
        m = re.search(r'name="SYNCHRONIZER_TOKEN" value="([^"]+)"', r.text)
        if m is None:
            raise RuntimeError("集保查詢頁取不到 SYNCHRONIZER_TOKEN（頁面結構可能改了）")
        self._token = m.group(1)
        # 下拉的資料日期即可查週次，新到舊
        self.available_dates = re.findall(r'<option value="(\d{8})"', r.text)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def _refresh_token(self) -> None:
        assert self._client is not None
        r = await self._client.get(_WEB_URL)
        r.raise_for_status()
        m = re.search(r'name="SYNCHRONIZER_TOKEN" value="([^"]+)"', r.text)
        if m is None:
            raise RuntimeError("集保查詢頁取不到 SYNCHRONIZER_TOKEN（頁面結構可能改了）")
        self._token = m.group(1)

    async def fetch_stock(self, symbol: str, sca_date: str) -> str:
        """單檔單週的查詢結果 HTML（sca_date 為 available_dates 內的 YYYYMMDD）。"""
        if self._client is None:
            raise RuntimeError("TdccWebClient 需以 async with 使用")
        if self._token is None:
            await self._refresh_token()
        r = await self._client.post(
            _WEB_URL,
            data={
                "SYNCHRONIZER_TOKEN": self._token,
                "SYNCHRONIZER_URI": "/portal/zh/smWeb/qryStock",
                "method": "submit",
                "firDate": self.available_dates[0] if self.available_dates else sca_date,
                "scaDate": sca_date,
                "sqlMethod": "StockNo",
                "stockNo": symbol,
                "stockName": "",
            },
        )
        r.raise_for_status()
        # 換發下一次要用的 token（用過的已失效）
        m = re.search(r'name="SYNCHRONIZER_TOKEN" value="([^"]+)"', r.text)
        self._token = m.group(1) if m else None
        return r.text
