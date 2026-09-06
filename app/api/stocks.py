"""個股籌碼分析 API（見 docs/05 §15）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import AnalysisResponse
from app.db.session import get_session
from app.repositories.features import FeatureDailyRepository
from app.services.analysis import AnalysisService

router = APIRouter(prefix="/api/stocks", tags=["stocks"])


@router.get("/{symbol}/analysis", response_model=AnalysisResponse)
async def get_analysis(
    symbol: str,
    session: AsyncSession = Depends(get_session),
) -> AnalysisResponse:
    repo = FeatureDailyRepository(session)
    fd = await repo.get_latest(symbol)
    if fd is None:
        raise HTTPException(status_code=404, detail=f"無 {symbol} 的特徵資料")
    result = AnalysisService().analyze(fd)
    return AnalysisResponse.from_result(result)
