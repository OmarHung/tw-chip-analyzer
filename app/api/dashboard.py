"""Dashboard 彙總 API（見 docs/05 §16）。

提供前端概覽：掃描標的數、各 action 家數、BUY/WATCH 候選數、平均分數、Top 榜。
含大盤（TAIEX 收盤/漲跌/均線/漲跌家數）與台指期主力月份（收盤/漲跌）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.repositories.market import (
    load_futures_front_month,
    load_market_daily,
    load_prev_taiex_close,
)
from app.services.market_scan import scan_all

router = APIRouter(prefix="/api", tags=["dashboard"])


class TopRow(BaseModel):
    symbol: str
    name: str
    chip_score: float
    change_pct: float | None = None
    action: str


class FuturesInfo(BaseModel):
    """台指期主力月份日盤收盤。change_pct 為小數（0.0084 = +0.84%）。"""

    contract_month: str
    close: float | None = None
    change: float | None = None
    change_pct: float | None = None


class MarketInfo(BaseModel):
    taiex_close: float | None = None
    taiex_change: float | None = None  # 點數
    taiex_change_pct: float | None = None  # 小數
    taiex_ma20: float | None = None
    taiex_ma60: float | None = None
    advancers: int | None = None
    decliners: int | None = None
    trend_score: float | None = None
    regime: str  # 多頭 / 偏多 / 中性 / 偏空 / 空頭
    futures: FuturesInfo | None = None


class DashboardResponse(BaseModel):
    as_of: str | None
    total: int
    action_counts: dict[str, int]
    buy_candidates: int
    watch_candidates: int
    avg_chip_score: float
    market: MarketInfo | None = None
    top: list[TopRow]


def _regime_label(score: float | None) -> str:
    if score is None:
        return "中性"
    if score >= 0.4:
        return "多頭"
    if score >= 0.15:
        return "偏多"
    if score > -0.15:
        return "中性"
    if score > -0.4:
        return "偏空"
    return "空頭"


@router.get("/dashboard", response_model=DashboardResponse)
async def dashboard(
    session: AsyncSession = Depends(get_session),
) -> DashboardResponse:
    as_of, rows = await scan_all(session)
    if as_of is None:
        return DashboardResponse(
            as_of=None, total=0, action_counts={}, buy_candidates=0,
            watch_candidates=0, avg_chip_score=0.0, top=[],
        )

    counts: dict[str, int] = {}
    for r in rows:
        counts[r.action] = counts.get(r.action, 0) + 1

    avg = round(sum(r.chip_score for r in rows) / len(rows), 1) if rows else 0.0
    top = sorted(rows, key=lambda x: x.chip_score, reverse=True)[:10]

    md = await load_market_daily(session, as_of)
    market = None
    if md is not None:
        close = float(md.taiex_close) if md.taiex_close is not None else None
        prev = await load_prev_taiex_close(session, as_of)
        # 前收缺（回補起點的第一天）或為 0 → 漲跌未知，不猜。
        change = close - prev if close is not None and prev else None
        fut = await load_futures_front_month(session, as_of)
        market = MarketInfo(
            taiex_close=close,
            taiex_change=round(change, 2) if change is not None else None,
            taiex_change_pct=round(change / prev, 6) if change is not None else None,
            futures=FuturesInfo(
                contract_month=fut.contract_month,
                close=float(fut.close) if fut.close is not None else None,
                change=float(fut.change) if fut.change is not None else None,
                change_pct=fut.change_pct,
            )
            if fut is not None
            else None,
            taiex_ma20=float(md.taiex_ma20) if md.taiex_ma20 is not None else None,
            taiex_ma60=float(md.taiex_ma60) if md.taiex_ma60 is not None else None,
            advancers=md.advancers,
            decliners=md.decliners,
            trend_score=md.market_trend_score,
            regime=_regime_label(md.market_trend_score),
        )

    return DashboardResponse(
        as_of=str(as_of),
        total=len(rows),
        action_counts=counts,
        buy_candidates=counts.get("BUY", 0),
        watch_candidates=counts.get("WATCH", 0),
        avg_chip_score=avg,
        market=market,
        top=[
            TopRow(
                symbol=r.symbol, name=r.name, chip_score=r.chip_score,
                change_pct=r.change_pct, action=r.action,
            )
            for r in top
        ],
    )
