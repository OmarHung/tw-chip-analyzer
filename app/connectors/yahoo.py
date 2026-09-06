"""Yahoo Finance chart connector（日K + 分時）。

Phase 1 的盤中/分時資料來源（Shioaji 需 production 權限，暫以 Yahoo 替代）。
台股代號後綴：上市 .TW、上櫃 .TWO。
"""
from __future__ import annotations

import datetime as dt

import httpx

BASE = "https://query1.finance.yahoo.com/v8/finance/chart"
_HEADERS = {"User-Agent": "Mozilla/5.0 (tw-chip-analyzer)"}


def yahoo_symbol(symbol: str, market: str | None) -> str:
    suffix = ".TWO" if (market or "").upper() == "TPEX" else ".TW"
    return f"{symbol}{suffix}"


async def _fetch(ysym: str, interval: str, range_: str) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(
            f"{BASE}/{ysym}",
            params={"interval": interval, "range": range_},
            headers=_HEADERS,
        )
        r.raise_for_status()
        return r.json()


def _parse(raw: dict, intraday: bool) -> tuple[list[dict], float | None]:
    result = (raw.get("chart") or {}).get("result") or []
    if not result:
        return [], None
    r = result[0]
    meta = r.get("meta", {})
    gmt = int(meta.get("gmtoffset", 28800))  # 台北 +8h
    prev_close = meta.get("chartPreviousClose")
    ts = r.get("timestamp") or []
    q = (r.get("indicators", {}).get("quote") or [{}])[0]
    o, h, l, c, v = (q.get(k) or [] for k in ("open", "high", "low", "close", "volume"))

    bars: list[dict] = []
    for i, t in enumerate(ts):
        close = c[i] if i < len(c) else None
        if close is None:
            continue
        if intraday:
            # lightweight-charts 以 UTC 顯示 → 加 gmtoffset 讓軸顯示台北時間
            time_val: int | str = int(t) + gmt
        else:
            time_val = dt.datetime.utcfromtimestamp(t + gmt).date().isoformat()
        bars.append(
            {
                "t": time_val,
                "o": o[i] if i < len(o) and o[i] is not None else close,
                "h": h[i] if i < len(h) and h[i] is not None else close,
                "l": l[i] if i < len(l) and l[i] is not None else close,
                "c": close,
                "v": int(v[i]) if i < len(v) and v[i] is not None else 0,
            }
        )
    return bars, prev_close


async def fetch_daily(symbol: str, market: str | None, range_: str = "3mo") -> list[dict]:
    raw = await _fetch(yahoo_symbol(symbol, market), "1d", range_)
    bars, _ = _parse(raw, intraday=False)
    return bars


async def fetch_intraday(
    symbol: str, market: str | None
) -> tuple[list[dict], float | None]:
    raw = await _fetch(yahoo_symbol(symbol, market), "1m", "1d")
    return _parse(raw, intraday=True)
