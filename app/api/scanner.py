"""全市場掃描 API（見 docs/05 §15）。

query：q（代號/名稱搜尋）/ min_score / action / min_turnover / industry / limit，依 chip_score 排序。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import ScannerResponse, ScannerRow
from app.db.session import get_session
from app.services.flow_scan import scan_divergence
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


class DivergenceRow(BaseModel):
    symbol: str
    name: str
    price: float | None
    change_pct: float | None
    turnover: float
    status: str
    label: str
    window: int
    price_return: float | None
    flow_ratio: float | None
    inst_net: float
    cost_state: str
    premium_pct: float | None


class DivergenceScanResponse(BaseModel):
    as_of: str | None
    window: int
    count: int
    rows: list[DivergenceRow]


# 排序權重：正背離越極端(價越跌 flow 越正)越前；負背離反之
_STATUS_RANK = {"bullish_div": 0, "aligned_up": 1, "neutral": 2, "aligned_down": 3, "bearish_div": 4}


@router.get("/scanner/divergence", response_model=DivergenceScanResponse)
async def scan_divergence_endpoint(
    session: AsyncSession = Depends(get_session),
    status: str | None = Query(None, description="bullish_div / bearish_div / ..."),
    window: int = Query(60, ge=10, le=120),
    min_turnover: float = Query(20_000_000, ge=0),
    limit: int = Query(50, ge=1, le=500),
) -> DivergenceScanResponse:
    """全市場 60 日量價背離掃描（已回測驗證方向性）。預設列正背離在前。"""
    as_of, rows = await scan_divergence(session, window=window)
    if as_of is None:
        return DivergenceScanResponse(as_of=None, window=window, count=0, rows=[])

    filtered = [
        r
        for r in rows
        if r.turnover >= min_turnover
        and (not status or r.status == status)
    ]

    def _key(r):
        # 正背離依 flow_ratio 由高到低（吸籌力道），負背離依 flow_ratio 由低到高
        rank = _STATUS_RANK.get(r.status, 2)
        fr = r.flow_ratio if r.flow_ratio is not None else 0.0
        return (rank, -fr if rank <= 1 else fr)

    filtered.sort(key=_key)
    out = [DivergenceRow(**vars(r)) for r in filtered[:limit]]
    return DivergenceScanResponse(
        as_of=str(as_of), window=window, count=len(out), rows=out
    )
