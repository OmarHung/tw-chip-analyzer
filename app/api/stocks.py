"""個股籌碼分析 API（見 docs/05 §15）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import AnalysisResponse
from app.connectors import yahoo
from app.core.logging import get_logger
from app.db.models.market import Stock
from app.db.session import get_session
from app.repositories.features import FeatureDailyRepository
from app.repositories.market import load_market_context
from app.services.analysis import AnalysisService

logger = get_logger("api.stocks")
router = APIRouter(prefix="/api/stocks", tags=["stocks"])


class Bar(BaseModel):
    t: str | int
    o: float
    h: float
    l: float
    c: float
    v: int


class ChartResponse(BaseModel):
    symbol: str
    name: str
    prev_close: float | None = None
    daily: list[Bar]
    intraday: list[Bar]


@router.get("/{symbol}/analysis", response_model=AnalysisResponse)
async def get_analysis(
    symbol: str,
    session: AsyncSession = Depends(get_session),
) -> AnalysisResponse:
    repo = FeatureDailyRepository(session)
    fd = await repo.get_latest(symbol)
    if fd is None:
        raise HTTPException(status_code=404, detail=f"無 {symbol} 的特徵資料")
    market = await load_market_context(session, fd.data_date)
    stock = await session.get(Stock, symbol)
    name = stock.name if stock else symbol
    result = AnalysisService().analyze(fd, market=market, name=name)
    return AnalysisResponse.from_result(result)


@router.get("/{symbol}/chart", response_model=ChartResponse)
async def get_chart(
    symbol: str,
    session: AsyncSession = Depends(get_session),
) -> ChartResponse:
    stock = await session.get(Stock, symbol)
    name = stock.name if stock else symbol
    market = stock.market if stock else "TWSE"
    try:
        daily = await yahoo.fetch_daily(symbol, market, range_="3mo")
        intraday, prev_close = await yahoo.fetch_intraday(symbol, market)
    except Exception as e:  # 外部來源失敗不應讓頁面掛掉
        logger.warning("Yahoo 圖表抓取失敗 %s: %s", symbol, e)
        daily, intraday, prev_close = [], [], None
    return ChartResponse(
        symbol=symbol, name=name, prev_close=prev_close,
        daily=[Bar(**b) for b in daily], intraday=[Bar(**b) for b in intraday],
    )
