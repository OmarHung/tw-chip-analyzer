"""Shioaji 行情連接器（逐筆 ticks）。

以 simulation=True 登入：下單為模擬，但行情資料為真實（此金鑰無 production 權限，
但模擬模式即可取得真實逐筆）。維持單一登入 session 重複使用；同步 API 於 thread 執行。
"""
from __future__ import annotations

import datetime as dt
import threading

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("connectors.shioaji")

_api = None
_lock = threading.Lock()


def _get_api():
    """惰性登入並快取 session（thread-safe）。"""
    global _api
    if _api is not None:
        return _api
    with _lock:
        if _api is not None:
            return _api
        import shioaji as sj

        s = get_settings()
        if not s.sj_api_key or not s.sj_sec_key:
            raise RuntimeError("未設定 Shioaji 金鑰")
        api = sj.Shioaji(simulation=True)
        api.login(api_key=s.sj_api_key, secret_key=s.sj_sec_key)
        logger.info("Shioaji 登入成功（simulation 行情模式）")
        _api = api
        return _api


def reset_api() -> None:
    """丟棄快取的 session，下次呼叫重新登入。

    token 過期 / session 斷線後，快取的 api 物件已無法使用且不會自行復原
    （實際事故：401 Token is expired 之後每次 usage() 都失敗直到容器重啟）。
    """
    global _api
    with _lock:
        _api = None


def usage_sync() -> dict | None:
    """回傳 Shioaji 資料用量 {bytes, limit_bytes, used_pct}；取不到回 None。

    供批次逐筆匯入監看配額，避免超量。api.usage() 為同步 API。
    """
    try:
        api = _get_api()
        u = api.usage()
    except Exception as e:  # noqa: BLE001 — 監看失敗不應中斷批次
        logger.warning("Shioaji usage() 取得失敗：%s", e)
        # session 可能已失效（token 過期 / 斷線）；丟棄以便下次重新登入。
        reset_api()
        return None
    used = getattr(u, "bytes", None)
    limit = getattr(u, "limit_bytes", None)
    pct = (used / limit * 100) if used is not None and limit else None
    return {"bytes": used, "limit_bytes": limit, "used_pct": pct}


# Shioaji tick_type → aggressor_side（1=買/外盤、-1=賣/內盤、0=無法判定）
_SIDE = {1: 1, 2: -1, 0: 0}


def fetch_ticks_sync(symbol: str, date: dt.date) -> list[dict]:
    """同步抓取單一標的某日逐筆。回傳 [{ts, price, volume, bid, ask, side}]。"""
    api = _get_api()
    contract = api.Contracts.Stocks.get(symbol)
    if contract is None:
        return []
    tk = api.ticks(contract, date=date.isoformat())
    ts = getattr(tk, "ts", []) or []
    close = getattr(tk, "close", []) or []
    volume = getattr(tk, "volume", []) or []
    bid = getattr(tk, "bid_price", []) or []
    ask = getattr(tk, "ask_price", []) or []
    ttype = getattr(tk, "tick_type", []) or []

    out: list[dict] = []
    for i in range(len(ts)):
        # ts 為奈秒 epoch（int）；以 UTC 解讀即為台北盤中時間（naive 台北牆鐘）
        raw = ts[i]
        when = (
            dt.datetime.utcfromtimestamp(raw / 1e9)
            if isinstance(raw, (int, float))
            else raw
        )
        out.append(
            {
                "ts": when,
                "price": float(close[i]),
                "volume": int(volume[i]),
                "bid": float(bid[i]) if i < len(bid) and bid[i] is not None else None,
                "ask": float(ask[i]) if i < len(ask) and ask[i] is not None else None,
                "side": _SIDE.get(int(ttype[i]) if i < len(ttype) else 0, 0),
            }
        )
    return out
