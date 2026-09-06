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


class FlowPoint(BaseModel):
    t: str  # data_date YYYY-MM-DD
    close: float | None = None
    # 三大法人買賣超（單位:張,買超為正）
    foreign: float | None = None  # 外資及陸資
    trust: float | None = None  # 投信
    dealer: float | None = None  # 自營商(自行+避險)
    inst_total: float | None = None  # 三大法人合計
    # 融資融券（單位:張）
    margin_balance: int | None = None  # 融資餘額
    short_balance: int | None = None  # 融券餘額


class TdccSnapshot(BaseModel):
    date: str
    retail_ratio: float | None = None
    medium_ratio: float | None = None
    large_ratio: float | None = None
    super_large_ratio: float | None = None
    holder_count: int | None = None


class FlowSummary(BaseModel):
    """尾端窗口的主力動向摘要（單位:張,買超為正）。"""

    foreign_5d: float
    foreign_20d: float
    foreign_60d: float
    inst_5d: float
    inst_20d: float
    inst_60d: float
    foreign_streak: int  # 外資連買(正)/連賣(負)天數
    margin_chg_20d: int  # 融資餘額近 20 交易日變化(張)


class DivergenceItem(BaseModel):
    window: int  # 觀察窗（交易日）
    price_return: float | None  # 區間報酬（小數）
    inst_net: float  # 區間三大法人淨買超（張）
    inst_flow_ratio: float | None  # 淨買超佔區間成交比重
    price_inst_corr: float | None  # 日價格變動 vs 日主力淨買超 相關係數
    status: str  # bullish_div / bearish_div / aligned_up / aligned_down / neutral
    label: str
    note: str


class FlowsResponse(BaseModel):
    symbol: str
    name: str
    count: int
    days: int
    points: list[FlowPoint]
    tdcc: TdccSnapshot | None = None
    summary: FlowSummary | None = None
    divergence: list[DivergenceItem] = []


def _to_lots(shares: int | None) -> float | None:
    """股數→張（1 張 = 1000 股），四捨五入至整數張。"""
    if shares is None:
        return None
    return round(shares / 1000)


def _tail_sum(vals: list[float | None], n: int) -> float:
    return sum(v for v in vals[-n:] if v is not None)


@router.get("/{symbol}/flows", response_model=FlowsResponse)
async def get_flows(
    symbol: str,
    days: int = 90,
    session: AsyncSession = Depends(get_session),
) -> FlowsResponse:
    """某檔近 days 日曆天的主力進出時序（三大法人買賣超 + 融資融券 + 價格）。

    展示用,採 data_date(不依 available_at 過濾,與走勢圖同源)。TDCC 目前僅單週快照,
    以最新一筆聚合呈現;盤中大單淨額由前端另呼叫 /orderflow 取得。
    """
    from app.repositories.market import (
        load_institutional_since,
        load_margin_since,
        load_prices_since,
        load_tdcc_summary_latest,
    )

    days = max(7, min(days, 365))
    latest = await _resolve_date(session, None)
    start = latest - dt.timedelta(days=days)

    inst_rows = await load_institutional_since(session, symbol, start)
    margin_rows = await load_margin_since(session, symbol, start)
    price_rows = await load_prices_since(session, symbol, start)

    close_by_date = {
        r.data_date: (float(r.close) if r.close is not None else None) for r in price_rows
    }
    vol_by_date = {
        r.data_date: (r.volume / 1000 if r.volume else None) for r in price_rows
    }
    margin_by_date = {r.data_date: r for r in margin_rows}

    # 以三大法人資料日為主軸（主力進出的核心來源）
    points: list[FlowPoint] = []
    inst_totals: list[float | None] = []
    foreigns: list[float | None] = []
    margin_bals: list[int | None] = []
    closes_arr: list[float | None] = []
    vols_arr: list[float | None] = []
    for r in inst_rows:
        foreign = _to_lots(r.foreign_net)
        trust = _to_lots(r.trust_net)
        dealer_parts = [r.dealer_self_net, r.dealer_hedge_net]
        dealer = (
            _to_lots(sum(p for p in dealer_parts if p is not None))
            if any(p is not None for p in dealer_parts)
            else None
        )
        inst_total = sum(v for v in (foreign, trust, dealer) if v is not None)
        m = margin_by_date.get(r.data_date)
        mb = m.margin_balance if m else None
        points.append(
            FlowPoint(
                t=str(r.data_date),
                close=close_by_date.get(r.data_date),
                foreign=foreign,
                trust=trust,
                dealer=dealer,
                inst_total=inst_total,
                margin_balance=mb,
                short_balance=m.short_balance if m else None,
            )
        )
        foreigns.append(foreign)
        inst_totals.append(inst_total)
        margin_bals.append(mb)
        closes_arr.append(close_by_date.get(r.data_date))
        vols_arr.append(vol_by_date.get(r.data_date))

    stock = await session.get(Stock, symbol)
    name = stock.name if stock else symbol

    tdcc_row = await load_tdcc_summary_latest(session, symbol)
    tdcc = None
    if tdcc_row is not None:
        def _pct(v: object) -> float | None:
            return float(v) if v is not None else None  # type: ignore[arg-type]

        tdcc = TdccSnapshot(
            date=str(tdcc_row.data_date),
            retail_ratio=_pct(tdcc_row.retail_ratio),
            medium_ratio=_pct(tdcc_row.medium_ratio),
            large_ratio=_pct(tdcc_row.large_ratio),
            super_large_ratio=_pct(tdcc_row.super_large_ratio),
            holder_count=tdcc_row.holder_count,
        )

    summary = None
    if points:
        # 外資連買/連賣天數（尾端同號連續段）
        streak = 0
        for v in reversed(foreigns):
            if v is None or v == 0:
                break
            if streak == 0:
                streak = 1 if v > 0 else -1
            elif (streak > 0) == (v > 0):
                streak += 1 if v > 0 else -1
            else:
                break
        window_bals = [b for b in margin_bals[-20:] if b is not None]
        margin_chg = (
            window_bals[-1] - window_bals[0] if len(window_bals) >= 2 else 0
        )
        summary = FlowSummary(
            foreign_5d=_tail_sum(foreigns, 5),
            foreign_20d=_tail_sum(foreigns, 20),
            foreign_60d=_tail_sum(foreigns, 60),
            inst_5d=_tail_sum(inst_totals, 5),
            inst_20d=_tail_sum(inst_totals, 20),
            inst_60d=_tail_sum(inst_totals, 60),
            foreign_streak=streak,
            margin_chg_20d=margin_chg,
        )

    # 量價背離偵測（config 驅動門檻，見 config/thresholds.yaml 的 divergence 段）
    from app.core.config import get_thresholds
    from app.services.flows import compute_divergence

    dcfg = get_thresholds().divergence
    windows = dcfg.get("windows", [20, 60])
    price_eps = float(dcfg.get("price_eps", 0.03))
    flow_eps = float(dcfg.get("flow_eps", 0.02))
    min_points = int(dcfg.get("min_points", 10))
    divergence: list[DivergenceItem] = []
    for w in windows:
        if len(points) < 2:
            break
        r = compute_divergence(
            closes_arr,
            inst_totals,
            vols_arr,
            window=int(w),
            price_eps=price_eps,
            flow_eps=flow_eps,
            min_points=min_points,
        )
        if r is not None:
            divergence.append(DivergenceItem(**r.__dict__))

    return FlowsResponse(
        symbol=symbol,
        name=name,
        count=len(points),
        days=days,
        points=points,
        tdcc=tdcc,
        summary=summary,
        divergence=divergence,
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
