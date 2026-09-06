"""全市場掃描 API（見 docs/05 §15）。

query：min_score / action / min_turnover / industry / limit，依 chip_score 排序。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import ScannerResponse, ScannerRow
from app.db.models.features import FeatureDaily
from app.db.models.market import Stock
from app.db.session import get_session
from app.repositories.features import FeatureDailyRepository
from app.services.analysis import AnalysisService

router = APIRouter(prefix="/api", tags=["scanner"])


@router.get("/scanner", response_model=ScannerResponse)
async def scan(
    session: AsyncSession = Depends(get_session),
    min_score: float = Query(0, ge=0, le=100),
    action: str | None = Query(None),
    min_turnover: float = Query(0, ge=0),
    industry: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
) -> ScannerResponse:
    repo = FeatureDailyRepository(session)
    as_of = await repo.latest_date()
    if as_of is None:
        return ScannerResponse(as_of=None, count=0, rows=[])

    stmt = (
        select(FeatureDaily, Stock.industry)
        .join(Stock, Stock.symbol == FeatureDaily.symbol)
        .where(FeatureDaily.data_date == as_of)
    )
    if industry:
        stmt = stmt.where(Stock.industry == industry)

    service = AnalysisService()
    rows: list[ScannerRow] = []
    for fd, _industry in (await session.execute(stmt)).all():
        r = service.analyze(fd)
        if r.chip.chip_score < min_score:
            continue
        if min_turnover and r.price and (fd.turnover or 0) < min_turnover:
            continue
        if action and r.signal.action.value != action.upper():
            continue
        rows.append(
            ScannerRow(
                symbol=r.symbol,
                price=r.price,
                chip_score=r.chip.chip_score,
                intraday=r.chip.intraday,
                institutional=r.chip.institutional,
                holder=r.chip.holder,
                action=r.signal.action.value,
                turnover=float(fd.turnover or 0),
                rr=r.signal.risk_reward,
            )
        )

    rows.sort(key=lambda x: x.chip_score, reverse=True)
    return ScannerResponse(as_of=str(as_of), count=len(rows[:limit]), rows=rows[:limit])
