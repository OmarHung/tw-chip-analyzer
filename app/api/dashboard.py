"""Dashboard 彙總 API（見 docs/05 §16）。

提供前端概覽：掃描標的數、各 action 家數、BUY/WATCH 候選數、平均分數、Top 榜。
註：TAIEX / 大盤 regime / 漲跌家數需大盤資料源，待 market 資料接入後補。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.services.market_scan import scan_all

router = APIRouter(prefix="/api", tags=["dashboard"])


class TopRow(BaseModel):
    symbol: str
    chip_score: float
    action: str


class DashboardResponse(BaseModel):
    as_of: str | None
    total: int
    action_counts: dict[str, int]
    buy_candidates: int
    watch_candidates: int
    avg_chip_score: float
    top: list[TopRow]


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
    return DashboardResponse(
        as_of=str(as_of),
        total=len(rows),
        action_counts=counts,
        buy_candidates=counts.get("BUY", 0),
        watch_candidates=counts.get("WATCH", 0),
        avg_chip_score=avg,
        top=[TopRow(symbol=r.symbol, chip_score=r.chip_score, action=r.action) for r in top],
    )
