"""個股籌碼分析 API（見 docs/05 §15）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

import datetime as dt

from app.api.schemas import AnalysisResponse
from app.connectors import yahoo
from app.core.logging import get_logger
from app.db.models.market import Stock
from app.db.session import get_session
from app.repositories.features import FeatureDailyRepository, SignalRepository
from app.repositories.market import load_daily_prices, load_market_context
from app.services.analysis import AnalysisService
from app.services.orderflow_intraday import compute_orderflow
from app.services.ticks import get_ticks

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


class Tick(BaseModel):
    t: int  # epoch 秒（台北時間以 UTC 表示）
    time: str  # HH:MM:SS
    price: float
    volume: int
    side: int  # 1=買/外盤, -1=賣/內盤, 0=無法判定
    bid: float | None = None
    ask: float | None = None


class TicksResponse(BaseModel):
    symbol: str
    date: str | None
    count: int
    ticks: list[Tick]


class CvdPoint(BaseModel):
    t: int
    cvd: float


class OrderFlowResponse(BaseModel):
    symbol: str
    date: str | None
    intraday_score: float
    net_aggressor: float
    large_net: float
    buy_ratio: float
    buy_volume: int
    sell_volume: int
    large_buy: float
    large_sell: float
    large_delta: float
    cvd_final: float
    trade_count: int
    total_volume: int
    cvd_series: list[CvdPoint]


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
    # 日 K:優先自家 daily_price(不依賴外部、與分析同源);缺才 fallback Yahoo。
    rows = await load_daily_prices(session, symbol)
    daily = [
        {
            "t": str(r.data_date),
            "o": float(r.open), "h": float(r.high),
            "l": float(r.low), "c": float(r.close),
            "v": int(r.volume or 0),
        }
        for r in rows
        if None not in (r.open, r.high, r.low, r.close)
    ]
    intraday: list[dict] = []
    prev_close: float | None = None
    try:
        if not daily:  # 自家無資料才打 Yahoo 日 K
            daily = await yahoo.fetch_daily(symbol, market, range_="1y")
        intraday, prev_close = await yahoo.fetch_intraday(symbol, market)
    except Exception as e:  # 外部來源失敗不應讓頁面掛掉
        logger.warning("Yahoo 圖表抓取失敗 %s: %s", symbol, e)
    # prev_close fallback:Yahoo intraday 失敗時,用日 K 倒數第二根收盤。
    if prev_close is None and len(daily) >= 2:
        prev_close = daily[-2]["c"]
    return ChartResponse(
        symbol=symbol, name=name, prev_close=prev_close,
        daily=[Bar(**b) for b in daily], intraday=[Bar(**b) for b in intraday],
    )


class ScorePoint(BaseModel):
    t: str  # data_date YYYY-MM-DD
    chip_score: float
    intraday: float | None = None
    institutional: float | None = None
    holder: float | None = None
    market: float | None = None
    action: str | None = None


class ScoreHistoryResponse(BaseModel):
    symbol: str
    name: str
    count: int
    points: list[ScorePoint]


@router.get("/{symbol}/scores", response_model=ScoreHistoryResponse)
async def get_score_history(
    symbol: str,
    session: AsyncSession = Depends(get_session),
) -> ScoreHistoryResponse:
    """Chip Score 與四維分項的每日時序(供走勢圖疊分數演變)。"""
    rows = await SignalRepository(session).list_history(symbol)
    stock = await session.get(Stock, symbol)
    name = stock.name if stock else symbol
    points = [
        ScorePoint(
            t=str(r.data_date),
            chip_score=float(r.chip_score),
            intraday=None if r.intraday_score is None else float(r.intraday_score),
            institutional=(
                None if r.institutional_score is None
                else float(r.institutional_score)
            ),
            holder=None if r.holder_score is None else float(r.holder_score),
            market=None if r.market_score is None else float(r.market_score),
            action=r.action,
        )
        for r in rows
    ]
    return ScoreHistoryResponse(
        symbol=symbol, name=name, count=len(points), points=points
    )


async def _resolve_date(session: AsyncSession, date: str | None) -> dt.date:
    if date:
        return dt.date.fromisoformat(date)
    from sqlalchemy import select as _select

    from app.db.models.market import DailyPrice

    return (
        await session.execute(
            _select(DailyPrice.data_date).order_by(DailyPrice.data_date.desc()).limit(1)
        )
    ).scalar_one_or_none() or dt.date.today()


@router.get("/{symbol}/ticks", response_model=TicksResponse)
async def get_stock_ticks(
    symbol: str,
    date: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> TicksResponse:
    target = await _resolve_date(session, date)
    ticks = await get_ticks(session, symbol, target)
    return TicksResponse(
        symbol=symbol,
        date=str(target),
        count=len(ticks),
        ticks=[Tick(**t) for t in ticks],
    )


@router.get("/{symbol}/orderflow", response_model=OrderFlowResponse)
async def get_stock_orderflow(
    symbol: str,
    date: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> OrderFlowResponse:
    target = await _resolve_date(session, date)
    ticks = await get_ticks(session, symbol, target)
    r = compute_orderflow(ticks)
    return OrderFlowResponse(
        symbol=symbol,
        date=str(target),
        intraday_score=r.intraday_score,
        net_aggressor=r.net_aggressor,
        large_net=r.large_net,
        buy_ratio=r.buy_ratio,
        buy_volume=r.buy_volume,
        sell_volume=r.sell_volume,
        large_buy=r.large_buy,
        large_sell=r.large_sell,
        large_delta=r.large_delta,
        cvd_final=r.cvd_final,
        trade_count=r.trade_count,
        total_volume=r.total_volume,
        cvd_series=[CvdPoint(**p) for p in r.cvd_series],
    )
