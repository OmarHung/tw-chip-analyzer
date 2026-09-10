"""手動回補執行器:單日 / 區間回補的背景任務 + 共用忙碌狀態。

- **共用單飛旗標**:手動回補與排程 EOD 共用 `_busy`,同時只允許一個工作;
  回補進行中排程 EOD 會跳過,反之手動回補在忙碌時被拒(避免資料/配額打架)。
- **背景執行 + 即時狀態**:`start_*` 立即返回,任務在 event loop 背景跑;
  `job_state()` 供 /api/ops/status 輪詢(狀態/步驟/進度/最後結果)。
- **範圍取捨**:區間回補只補日線/法人/特徵(不含逐筆),避免區間逐筆燒穿
  Shioaji 配額;逐筆僅限單日。

註:依 CLAUDE.md,排程器所在 API 以單 worker 執行,故 module 級狀態即為全域真相。
"""
from __future__ import annotations

import asyncio
import datetime as dt
from zoneinfo import ZoneInfo

from app.core.logging import get_logger
from app.jobs import daily, import_ticks

logger = get_logger("jobs.runner")
_TZ = ZoneInfo("Asia/Taipei")

_busy = False
_task: asyncio.Task | None = None
_state: dict = {
    "state": "idle",       # idle | running | done | error
    "kind": None,          # single | range | scheduled_eod
    "mode": None,          # eod | ticks | daily
    "target": None,        # "2026-09-07" 或 "2026-06-01~2026-09-07"
    "step": None,
    "progress": None,      # {done,total,...}
    "started_at": None,
    "finished_at": None,
    "result": None,
    "error": None,
}


def job_state() -> dict:
    return dict(_state)


def is_busy() -> bool:
    return _busy


def _now() -> str:
    return dt.datetime.now(_TZ).isoformat(timespec="seconds")


def try_mark(kind: str, mode: str, target: str) -> bool:
    """搶佔忙碌旗標並初始化狀態;已忙碌回 False。同步呼叫,無 race。"""
    global _busy
    if _busy:
        return False
    _busy = True
    _state.update(
        state="running", kind=kind, mode=mode, target=target,
        step="準備中", progress=None, started_at=_now(),
        finished_at=None, result=None, error=None,
    )
    return True


def finish(ok: bool = True, error: str | None = None) -> None:
    global _busy
    _state.update(
        state="done" if ok else "error",
        step=None, finished_at=_now(), error=error,
    )
    _busy = False


def _weekdays(start: dt.date, end: dt.date) -> list[dt.date]:
    """start~end(含)之間的平日(週一~週五);非交易日 daily 會自動空跑。"""
    out: list[dt.date] = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


async def _do_single(target: dt.date, mode: str) -> None:
    """單日回補:mode='eod' 走完整流程;mode='ticks' 只補逐筆 + 重建特徵。"""
    def _prog(p: dict) -> None:
        _state["progress"] = p

    if mode == "eod":
        _state["step"] = f"{target}:匯入行情/法人/融資/TDCC/TAIEX + 特徵"
        await daily.run(
            target, do_import=True, do_features=True, do_signals=False,
            do_tdcc=True, do_index=True,
        )

    _state["step"] = f"{target}:逐筆匯入(Shioaji)"
    try:
        tick_res = await import_ticks.run(target, on_progress=_prog)
    except Exception as e:  # noqa: BLE001 — 逐筆非必要,不中斷整體
        logger.warning("回補逐筆失敗:%s", e)
        tick_res = {"error": str(e)}

    _state["step"] = f"{target}:重建特徵 + 落地分數"
    _state["progress"] = None
    await daily.run(target, do_import=False, do_features=True, do_signals=True)

    _state["result"] = {"date": str(target), "mode": mode, "ticks": tick_res}


async def _do_range(start: dt.date, end: dt.date) -> None:
    """區間回補:每個平日補日線/法人/融資 + 特徵 + 落地(不含逐筆)。"""
    days = _weekdays(start, end)
    total = len(days)
    done = 0
    for d in days:
        _state["step"] = f"回補 {d}(日線/法人,{done + 1}/{total})"
        _state["progress"] = {"done": done, "total": total}
        await daily.run(
            d, do_import=True, do_features=True, do_signals=True,
            do_tdcc=False, do_index=False,
        )
        done += 1
    _state["progress"] = {"done": done, "total": total}
    _state["result"] = {"range": f"{start}~{end}", "days": done}


async def _do_days(days: list[dt.date], label: str) -> None:
    """補指定的幾個交易日(不必連續)。用於「補齊缺漏」——只跑真的缺的那幾天,
    不像區間回補會把中間已有資料的日子重跑一遍。內容同區間:日線/法人/融資
    + 特徵 + 落地,不含逐筆(逐筆受 Shioaji 配額限制,只走單日)。"""
    total = len(days)
    done = 0
    for d in days:
        _state["step"] = f"補齊 {d}({done + 1}/{total})"
        _state["progress"] = {"done": done, "total": total}
        await daily.run(
            d, do_import=True, do_features=True, do_signals=True,
            do_tdcc=False, do_index=False,
        )
        done += 1
    _state["progress"] = {"done": done, "total": total}
    _state["result"] = {"missing": label, "days": done}


def start_days(days: list[dt.date]) -> bool:
    global _task
    if not days:
        return False
    label = f"{days[0]}~{days[-1]}" if len(days) > 1 else str(days[0])
    if not try_mark("missing", "daily", label):
        return False
    _task = asyncio.create_task(_guarded(_do_days(days, label)))
    return True


async def _guarded(coro) -> None:
    try:
        await coro
        finish(ok=True)
    except Exception as e:  # noqa: BLE001
        logger.exception("回補失敗")
        finish(ok=False, error=str(e))


def start_single(target: dt.date, mode: str) -> bool:
    global _task
    if not try_mark("single", mode, str(target)):
        return False
    _task = asyncio.create_task(_guarded(_do_single(target, mode)))
    return True


def start_range(start: dt.date, end: dt.date) -> bool:
    global _task
    if not try_mark("range", "daily", f"{start}~{end}"):
        return False
    _task = asyncio.create_task(_guarded(_do_range(start, end)))
    return True
