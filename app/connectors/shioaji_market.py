"""Shioaji 行情連接器（逐筆 ticks）。

以 simulation=True 登入：下單為模擬，但行情資料為真實（此金鑰無 production 權限，
但模擬模式即可取得真實逐筆）。維持單一登入 session 重複使用；同步 API 於 thread 執行。

**callback 訂閱骨架（2026-09-14 spike，docs/16 §A1）**：`subscribe_ticks` /
`unsubscribe_ticks` 只負責訂閱管理，複用既有 `_get_api()` 單例 session（不另建
連線），驗證「同一個 session 能否同時服務既有同步 `api.ticks()` 歷史查詢與新的
callback 即時訂閱」。**只做訂閱管理，不含 buffer/flush**（那是 A2 的事）——callback
內僅做型別轉換 + 呼叫呼叫端傳入的 `on_tick`，不寫 DB、不算分數（鐵則 11）。禁止呼叫
任何下單/改單/刪單 API（鐵則 1）。
"""
from __future__ import annotations

import datetime as dt
import threading
from collections.abc import Callable
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("connectors.shioaji")

_api = None
_lock = threading.Lock()

# 訂閱檔數上限（spike 階段的硬編碼節流，非評分 threshold；正式配額治理見 docs/16 §A3/A4）。
MAX_SUBSCRIBE_SYMBOLS = 3


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


# ---------------------------------------------------------------------------
# Callback 訂閱骨架（spike，docs/16 §A1）——僅訂閱管理，不含 buffer/flush。
# ---------------------------------------------------------------------------

# symbol → 呼叫端提供的 on_tick 回呼；由單一已註冊的 SDK callback（_dispatch_tick）
# 依 tick.code 分派。用獨立的 _sub_lock（非 _get_api 用的 _lock）保護，避免訂閱管理
# 與登入初始化互相卡住。
_tick_callbacks: dict[str, Callable[[dict], None]] = {}
_sub_lock = threading.Lock()
_callback_registered = False


def _stream_tick_to_dict(tick: Any) -> dict:
    """轉換 streaming TickSTKv1 → dict。

    注意：streaming tick（`on_tick_stk_v1`）與 `fetch_ticks_sync` 用的批次歷史
    `api.ticks()` 是不同的資料形狀——streaming tick 沒有 bid/ask（五檔要另外訂閱
    `QuoteType.BidAsk` 走 `on_bidask_stk_v1`），`tick.datetime` 已是 Python
    datetime（非 epoch ns，不需要 `utcfromtimestamp` 轉換）。
    """
    return {
        "code": getattr(tick, "code", None),
        "ts": getattr(tick, "datetime", None),
        "price": float(tick.close),
        "volume": int(tick.volume),
        "bid": None,
        "ask": None,
        "side": _SIDE.get(int(getattr(tick, "tick_type", 0) or 0), 0),
    }


def _dispatch_tick(tick: Any) -> None:
    """SDK callback 進入點：只做型別轉換 + 分派給對應 symbol 的 on_tick（鐵則 11：

    這裡禁止 DB 寫入 / pandas 運算 / HTTP 呼叫 / scoring；那些留給呼叫端的 on_tick
    自行決定要不要做（本 spike 的呼叫端也只做 log / append list，見驗證腳本）。
    """
    code = getattr(tick, "code", None)
    with _sub_lock:
        cb = _tick_callbacks.get(code)
    if cb is None:
        return
    try:
        cb(_stream_tick_to_dict(tick))
    except Exception:  # noqa: BLE001 — 呼叫端 callback 出錯不可打斷 SDK 派送迴圈
        logger.exception("tick callback 執行失敗（symbol=%s）", code)


def subscribed_symbols() -> list[str]:
    """目前已訂閱（本 process 內已知）的標的清單，供驗證/監看用。"""
    with _sub_lock:
        return sorted(_tick_callbacks)


def subscribe_ticks(
    symbols: list[str], on_tick: Callable[[dict], None]
) -> list[str]:
    """訂閱逐筆 tick callback（複用既有 `_get_api()` session，不另建連線）。

    範圍限制在 `MAX_SUBSCRIBE_SYMBOLS` 檔以內（spike 節流策略，見 docs/16 §A4）。
    `on_tick` 收到的是 `_stream_tick_to_dict` 轉換後的 dict；callback 內不可做
    DB 寫入 / heavy 運算（鐵則 11），此函式本身也不做（只註冊 + 呼叫 SDK subscribe）。

    回傳實際訂閱成功的 symbol 清單（略過查無合約的 symbol）。
    """
    if not symbols:
        return []
    if len(symbols) > MAX_SUBSCRIBE_SYMBOLS:
        raise ValueError(
            f"訂閱檔數 {len(symbols)} 超過 spike 節流上限 {MAX_SUBSCRIBE_SYMBOLS}"
            "（避免燒穿模擬帳號配額，見 docs/16-phase2-realtime-ml-plan.md §A.4）"
        )

    import shioaji as sj

    api = _get_api()

    global _callback_registered
    with _sub_lock:
        if not _callback_registered:
            # 先註冊 callback 再訂閱，避免剛訂閱後的事件漏接（STREAMING.md 建議順序）。
            api.set_on_tick_stk_v1_callback(_dispatch_tick)
            _callback_registered = True

    subscribed: list[str] = []
    for symbol in symbols:
        contract = api.Contracts.Stocks.get(symbol)
        if contract is None:
            logger.warning("訂閱失敗：查無合約 symbol=%s", symbol)
            continue
        api.subscribe(contract, quote_type=sj.QuoteType.Tick)
        with _sub_lock:
            _tick_callbacks[symbol] = on_tick
        subscribed.append(symbol)
        logger.info("已訂閱逐筆 tick：%s", symbol)
    return subscribed


def unsubscribe_ticks(symbols: list[str]) -> None:
    """取消訂閱逐筆 tick callback；對未訂閱的 symbol 為 no-op。"""
    if not symbols:
        return

    import shioaji as sj

    api = _get_api()
    for symbol in symbols:
        contract = api.Contracts.Stocks.get(symbol)
        if contract is None:
            logger.warning("取消訂閱失敗：查無合約 symbol=%s", symbol)
            continue
        try:
            api.unsubscribe(contract, quote_type=sj.QuoteType.Tick)
        except Exception:  # noqa: BLE001 — 取消訂閱失敗不應中斷後續 symbol 的清理
            logger.warning("取消訂閱 %s 時發生例外", symbol, exc_info=True)
        with _sub_lock:
            _tick_callbacks.pop(symbol, None)
        logger.info("已取消訂閱逐筆 tick：%s", symbol)
