"""全市場掃描 API（見 docs/05 §15）。

query：q（代號/名稱搜尋）/ min_score / action / min_turnover / industry / limit，依 chip_score 排序。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import ScannerResponse, ScannerRow
from app.db.session import get_session
from app.services.market_scan import scan_all

router = APIRouter(prefix="/api", tags=["scanner"])


@router.get("/scanner", response_model=ScannerResponse)
async def scan(
    session: AsyncSession = Depends(get_session),
    q: str | None = Query(None),
    min_score: float = Query(0, ge=0, le=100),
    action: str | None = Query(None),
    min_turnover: float = Query(0, ge=0),
    industry: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
) -> ScannerResponse:
    as_of, rows = await scan_all(session)
    if as_of is None:
        return ScannerResponse(as_of=None, count=0, rows=[])

    keyword = q.strip().lower() if q else ""
    filtered = [
        r
        for r in rows
        if r.chip_score >= min_score
        and (not min_turnover or r.turnover >= min_turnover)
        and (not action or r.action == action.upper())
        and (not industry or r.industry == industry)
        and (not keyword or keyword in r.symbol.lower() or keyword in r.name.lower())
    ]
    filtered.sort(key=lambda x: x.chip_score, reverse=True)
    out = [
        ScannerRow(
            symbol=r.symbol, name=r.name, price=r.price, change_pct=r.change_pct,
            chip_score=r.chip_score, intraday=r.intraday,
            institutional=r.institutional, holder=r.holder,
            action=r.action, turnover=r.turnover, rr=r.rr,
        )
        for r in filtered[:limit]
    ]
    return ScannerResponse(as_of=str(as_of), count=len(out), rows=out)
