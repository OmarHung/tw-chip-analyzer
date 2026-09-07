"""系統狀態 API(唯讀):Shioaji 配額、資料涵蓋度、EOD 排程現況。

前端「系統」頁用。配額來自 shioaji_market.usage_sync()(同步 + 會觸發登入),
故以 to_thread 執行並加 TTL 快取,避免每次刷頁都連線/耗資源。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_thresholds
from app.core.logging import get_logger
from app.db.session import get_session
from app.jobs import runner
from app.jobs.scheduler import scheduler_status
from app.repositories.ops import load_coverage

logger = get_logger("api.ops")

router = APIRouter(prefix="/api/ops", tags=["ops"])

# 區間回補平日數上限(避免一次排入過長、跑數小時)。
_RANGE_MAX_WEEKDAYS = 90

# 配額 TTL 快取(秒):usage() 會連 Shioaji,不宜每次刷頁都打。
_QUOTA_TTL = 60.0
_quota_cache: dict | None = None
_quota_at: float = 0.0
_QUOTA_TIMEOUT = 8.0  # 連線/登入逾時,避免拖垮頁面


class Quota(BaseModel):
    available: bool
    bytes: int | None = None
    limit_bytes: int | None = None
    used_pct: float | None = None
    cached_age_sec: int | None = None


class OpsStatus(BaseModel):
    quota: Quota
    coverage: dict
    schedule: dict
    job: dict


class BackfillRequest(BaseModel):
    kind: str  # "single" | "range"
    date: str | None = None            # single 用
    mode: str | None = "eod"           # single 用:"eod" | "ticks"
    start: str | None = None           # range 用
    end: str | None = None             # range 用


async def _get_quota() -> Quota:
    global _quota_cache, _quota_at
    now = time.monotonic()
    if _quota_cache is not None and (now - _quota_at) < _QUOTA_TTL:
        return Quota(**_quota_cache, cached_age_sec=int(now - _quota_at))

    from app.connectors.shioaji_market import usage_sync

    try:
        u = await asyncio.wait_for(asyncio.to_thread(usage_sync), _QUOTA_TIMEOUT)
    except (asyncio.TimeoutError, Exception) as e:  # noqa: BLE001 — 監看失敗不應報錯
        logger.warning("配額查詢失敗:%s", e)
        u = None

    if u is None:
        # 取不到:不快取(下次再試),回 available=false
        return Quota(available=False)

    _quota_cache = {
        "available": True,
        "bytes": u.get("bytes"),
        "limit_bytes": u.get("limit_bytes"),
        "used_pct": round(u["used_pct"], 1) if u.get("used_pct") is not None else None,
    }
    _quota_at = now
    return Quota(**_quota_cache, cached_age_sec=0)


@router.get("/status", response_model=OpsStatus)
async def status(session: AsyncSession = Depends(get_session)) -> OpsStatus:
    quota = await _get_quota()
    coverage = await load_coverage(session)
    return OpsStatus(
        quota=quota,
        coverage=coverage,
        schedule=scheduler_status(),
        job=runner.job_state(),
    )


def _parse_date(s: str | None, label: str) -> dt.date:
    if not s:
        raise HTTPException(status_code=400, detail=f"缺少{label}")
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{label}格式錯誤(需 YYYY-MM-DD)")


@router.post("/backfill", response_model=OpsStatus)
async def backfill(
    req: BackfillRequest, session: AsyncSession = Depends(get_session)
) -> OpsStatus:
    """觸發回補(背景執行)。單日可選完整 EOD / 只逐筆;區間只補日線/法人。"""
    if runner.is_busy():
        raise HTTPException(status_code=409, detail="已有回補/EOD 工作進行中,請稍候")

    if req.kind == "single":
        target = _parse_date(req.date, "日期")
        mode = req.mode or "eod"
        if mode not in ("eod", "ticks"):
            raise HTTPException(status_code=400, detail="mode 需為 eod 或 ticks")
        # 只逐筆模式:配額已滿時擋下(EOD 模式仍放行,逐筆段會自行在 95% 停)
        if mode == "ticks":
            stop_pct = float(get_thresholds().intraday_batch.get("usage_stop_pct", 95))
            q = await _get_quota()
            if q.available and q.used_pct is not None and q.used_pct >= stop_pct:
                raise HTTPException(
                    status_code=409,
                    detail=f"Shioaji 配額已達 {q.used_pct}%(≥{stop_pct}%),"
                    "逐筆回補會立即中止;請等每日配額回補後再試。",
                )
        if not runner.start_single(target, mode):
            raise HTTPException(status_code=409, detail="工作啟動失敗(忙碌中)")
    elif req.kind == "range":
        start = _parse_date(req.start, "起始日")
        end = _parse_date(req.end, "結束日")
        if end < start:
            raise HTTPException(status_code=400, detail="結束日不可早於起始日")
        weekdays = len(runner._weekdays(start, end))
        if weekdays == 0:
            raise HTTPException(status_code=400, detail="區間內無平日")
        if weekdays > _RANGE_MAX_WEEKDAYS:
            raise HTTPException(
                status_code=400,
                detail=f"區間過長({weekdays} 平日 > 上限 {_RANGE_MAX_WEEKDAYS}),請縮小範圍",
            )
        if not runner.start_range(start, end):
            raise HTTPException(status_code=409, detail="工作啟動失敗(忙碌中)")
    else:
        raise HTTPException(status_code=400, detail="kind 需為 single 或 range")

    return OpsStatus(
        quota=await _get_quota(),
        coverage=await load_coverage(session),
        schedule=scheduler_status(),
        job=runner.job_state(),
    )
