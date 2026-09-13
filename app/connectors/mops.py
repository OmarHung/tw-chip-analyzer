"""MOPS Phase 2 connector（只負責抓取 raw，解析在 app/importers/mops.py）。端點查證見 docs/14。

錯誤分類：
- MopsTransientError：逾時、連線重置、5xx、回應被截斷——依 config 重試後仍失敗才拋出。
- MopsBlockedError：官方「因為安全性考量…無法呈現」阻擋頁或 403——**不重試**（再打只會更糟）。
- MopsHttpError：其餘 4xx 等非暫時性錯誤。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
from collections.abc import Awaitable, Callable

import httpx

from app.connectors.tpex import _ssl_ctx as _tpex_ssl_ctx
from app.core.config import get_thresholds
from app.core.logging import get_logger
from app.importers.mops import MARKET_TPEX, MARKET_TWSE

logger = get_logger("connectors.mops")

_HEADERS = {"User-Agent": "Mozilla/5.0 (tw-chip-analyzer)"}
_MOPS_WEB = "https://mopsov.twse.com.tw/mops/web"
_OPENAPI = {
    ("holdings", MARKET_TWSE): "https://openapi.twse.com.tw/v1/opendata/t187ap11_L",
    ("holdings", MARKET_TPEX): "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap11_O",
    ("transfers", MARKET_TWSE): "https://openapi.twse.com.tw/v1/opendata/t187ap12_L",
    ("transfers", MARKET_TPEX): "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap12_O",
}
_WEB_TYPEK = {MARKET_TWSE: "sii", MARKET_TPEX: "otc"}
_WEB_TRANSFER_REPORT = {MARKET_TWSE: "SY", MARKET_TPEX: "OY"}
_BLOCK_MARKERS = ("因為安全性考量", "FOR SECURITY REASONS")


class MopsError(Exception):
    pass


class MopsTransientError(MopsError):
    pass


class MopsBlockedError(MopsError):
    pass


class MopsHttpError(MopsError):
    pass


def _http_cfg() -> dict:
    return get_thresholds().get("mops", "http", default={}) or {}


def throttle_sec() -> float:
    return float(_http_cfg().get("throttle_sec", 1.5))


def _check_blocked(text: str, url: str) -> None:
    if any(m in text for m in _BLOCK_MARKERS):
        raise MopsBlockedError(f"MOPS 安全性阻擋頁：{url}")


async def with_retry(
    call: Callable[[], Awaitable[str]], label: str, *,
    retries: int | None = None, backoff_sec: float | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> str:
    """暫時性錯誤重試（第 n 次前等 backoff*n 秒）；阻擋與非暫時性錯誤立即拋出。"""
    cfg = _http_cfg()
    retries = int(cfg.get("retries", 3)) if retries is None else retries
    backoff = float(cfg.get("backoff_sec", 2.0)) if backoff_sec is None else backoff_sec
    attempt = 0
    while True:
        try:
            return await call()
        except MopsTransientError as e:
            attempt += 1
            if attempt > retries:
                logger.error("%s 重試 %d 次仍失敗：%s", label, retries, e)
                raise
            logger.warning("%s 暫時性失敗（第 %d/%d 次重試）：%s", label, attempt, retries, e)
            await sleep(backoff * attempt)


async def _request(method: str, url: str, *, data: dict | None = None, verify=True) -> str:
    timeout = float(_http_cfg().get("timeout_sec", 60))
    try:
        async with httpx.AsyncClient(timeout=timeout, headers=_HEADERS, verify=verify) as client:
            r = await client.request(method, url, data=data)
    except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as e:
        raise MopsTransientError(f"{type(e).__name__}: {url}") from e
    if r.status_code >= 500:
        raise MopsTransientError(f"HTTP {r.status_code}: {url}")
    if r.status_code == 403:
        raise MopsBlockedError(f"HTTP 403: {url}")
    if r.status_code >= 400:
        raise MopsHttpError(f"HTTP {r.status_code}: {url}")
    _check_blocked(r.text, url)
    return r.text


async def _openapi(kind: str, market: str) -> list[dict]:
    url = _OPENAPI[(kind, market)]
    verify = _tpex_ssl_ctx if market == MARKET_TPEX else True

    async def call() -> str:
        text = await _request("GET", url, verify=verify)
        try:
            json.loads(text)
        except json.JSONDecodeError as e:  # TPEx 實測常在傳輸中途截斷
            raise MopsTransientError(f"JSON 不完整（{len(text)} bytes）：{url}") from e
        return text

    data = json.loads(await with_retry(call, f"OpenAPI {kind} {market}"))
    if not isinstance(data, list):
        raise MopsHttpError(f"OpenAPI 回應不是陣列：{url}")
    return data


async def fetch_openapi_holdings(market: str) -> list[dict]:
    """董監事持股餘額明細（最新一期，無日期參數，不可回補）。"""
    return await _openapi("holdings", market)


async def fetch_openapi_transfers(market: str) -> list[dict]:
    """內部人持股轉讓事前申報日報表（最新一日，不可回補）。"""
    return await _openapi("transfers", market)


async def fetch_holdings_page(symbol: str, market: str, year: int, month: int) -> str:
    """MOPS ajax_stapap1：單公司單月持股（year 為西元，內部轉民國）。"""
    url = f"{_MOPS_WEB}/ajax_stapap1"
    data = {
        "encodeURIComponent": "1", "step": "1", "firstin": "true", "off": "1",
        "TYPEK": _WEB_TYPEK[market], "year": str(year - 1911), "month": f"{month:02d}",
        "co_id": symbol,
    }
    return await with_retry(
        lambda: _request("POST", url, data=data), f"持股頁 {symbol} {year}-{month:02d}"
    )


async def fetch_transfer_page(report_date: dt.date, market: str) -> str:
    """MOPS ajax_t56sb12：單日單市場轉讓事前申報（report=SY 上市 / OY 上櫃）。"""
    url = f"{_MOPS_WEB}/ajax_t56sb12"
    data = {
        "encodeURIComponent": "1", "run": "", "step": "2",
        "year": str(report_date.year - 1911), "month": f"{report_date.month:02d}",
        "day": f"{report_date.day:02d}", "report": _WEB_TRANSFER_REPORT[market],
        "firstin": "true",
    }
    return await with_retry(
        lambda: _request("POST", url, data=data), f"轉讓申報頁 {report_date} {market}"
    )
