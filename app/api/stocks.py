"""個股籌碼分析 API（見 docs/05 §15）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import datetime as dt

from app.api.schemas import AnalysisResponse
from app.connectors import yahoo
from app.core.logging import get_logger
from app.db.models.market import CorporateAction, DailyPrice, Stock
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
    # percentile mapping 需同日全市場橫斷面(與 scanner/snapshot 同一真相);
    # 載入當日全部 feature 一起算,再挑出本檔。
    from sqlalchemy import select as _select

    from app.db.models.features import FeatureDaily
    from app.services.analysis import analyze_market

    day_rows = (
        await session.execute(
            _select(FeatureDaily).where(FeatureDaily.data_date == fd.data_date)
        )
    ).scalars().all()
    items: list[tuple[FeatureDaily, str | None]] = [
        (x, name if x.symbol == symbol else None) for x in day_rows
    ]
    results = analyze_market(AnalysisService(), items, market)
    result = next(r for r in results if r.symbol == symbol)
    resp = AnalysisResponse.from_result(result)
    # 市場別（上市/上櫃）只在 stock 主檔，AnalysisResult 不帶，於此補上供前端標示。
    resp.market = stock.market if stock else None
    return resp


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


class CorporateActionItem(BaseModel):
    date: str
    kind: str  # 權 / 息 / 權息 / 面額 / 減資
    prev_close: float | None = None
    reference_price: float | None = None
    adj_factor: float | None = None  # 參考價/前收（價格還原因子）
    share_factor: float | None = None  # 1 舊股→幾新股（量還原因子）


class FeaturesResponse(BaseModel):
    """feature_daily 的還原後價格特徵 + 造成還原的公司行動（供人工核對）。"""

    symbol: str
    name: str
    date: str
    close: float | None = None  # 還原後收盤（=當日原始收盤，因後復權最新一根不動）
    raw_close: float | None = None  # daily_price 原始收盤
    change_pct: float | None = None
    ma20: float | None = None  # 還原後 20 日均價
    atr14: float | None = None  # 還原後 ATR14
    vwap: float | None = None
    recent_swing_low: float | None = None
    close_vs_ma20_pct: float | None = None
    close_vs_vwap_pct: float | None = None
    actions: list[CorporateActionItem] = []


def _f(v: object) -> float | None:
    return float(v) if v is not None else None  # type: ignore[arg-type]


@router.get("/{symbol}/features", response_model=FeaturesResponse)
async def get_features(
    symbol: str,
    date: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> FeaturesResponse:
    """某檔某日的還原後價格特徵（date 省略=該檔最新一日）。

    `chart` 端點刻意回原始價，還原值只存在 feature_daily；AVOID 股的 `/analysis` 也不
    輸出 MA/ATR。此端點讓公司行動的還原效果可被直接核對（例：6949 面額 1:20 變更後，
    close_vs_ma20_pct 應 ≈0 而非 ≈-94%）。
    """
    repo = FeatureDailyRepository(session)
    fd = (
        await repo.get_on_date(symbol, dt.date.fromisoformat(date))
        if date
        else await repo.get_latest(symbol)
    )
    if fd is None:
        raise HTTPException(
            status_code=404, detail=f"無 {symbol} 於 {date or '最新日'} 的特徵資料"
        )

    stock = await session.get(Stock, symbol)
    raw = (
        await session.execute(
            select(DailyPrice.close).where(
                DailyPrice.symbol == symbol, DailyPrice.data_date == fd.data_date
            )
        )
    ).scalar_one_or_none()
    # 只列「實際影響這天特徵」的事件：feature_builder 的 45 日視窗內、且 <=當日。
    rows = (
        await session.execute(
            select(CorporateAction)
            .where(
                CorporateAction.symbol == symbol,
                CorporateAction.data_date > fd.data_date - dt.timedelta(days=45),
                CorporateAction.data_date <= fd.data_date,
            )
            .order_by(CorporateAction.data_date)
        )
    ).scalars().all()

    return FeaturesResponse(
        symbol=symbol,
        name=stock.name if stock else symbol,
        date=str(fd.data_date),
        close=_f(fd.close),
        raw_close=_f(raw),
        change_pct=fd.change_pct,
        ma20=_f(fd.ma20),
        atr14=_f(fd.atr14),
        vwap=_f(fd.vwap),
        recent_swing_low=_f(fd.recent_swing_low),
        close_vs_ma20_pct=fd.close_vs_ma20_pct,
        close_vs_vwap_pct=fd.close_vs_vwap_pct,
        actions=[
            CorporateActionItem(
                date=str(r.data_date),
                kind=r.kind,
                prev_close=_f(r.prev_close),
                reference_price=_f(r.reference_price),
                adj_factor=_f(r.adj_factor),
                share_factor=_f(r.share_factor),
            )
            for r in rows
        ],
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


class CostBasisPoint(BaseModel):
    t: str
    cost: float | None = None  # 估算主力平均成本（元）


class CostBasis(BaseModel):
    points: list[CostBasisPoint]
    latest_cost: float | None = None
    latest_price: float | None = None
    premium_pct: float | None = None  # 現價/成本-1（正=浮盈）
    state: str  # profit / loss / flat / unknown
    label: str


class FlowsResponse(BaseModel):
    symbol: str
    name: str
    count: int
    days: int
    points: list[FlowPoint]
    tdcc: TdccSnapshot | None = None
    summary: FlowSummary | None = None
    divergence: list[DivergenceItem] = []
    cost_basis: CostBasis | None = None


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
    # 每日成交均價（VWAP=成交金額/成交量；缺則退回收盤）供主力成本估算
    def _vwap(r: object) -> float | None:
        turnover = getattr(r, "turnover", None)
        volume = getattr(r, "volume", None)
        if turnover and volume:
            return float(turnover) / volume
        return float(r.close) if r.close is not None else None  # type: ignore[attr-defined]

    vwap_by_date = {r.data_date: _vwap(r) for r in price_rows}
    margin_by_date = {r.data_date: r for r in margin_rows}

    # 公司行動還原（拆股/除權息/減資）：主力成本/股價線/累積買賣超都是即時算、原本走
    # 原始 daily_price，跨拆股會斷點（如 6949 面額 1:20，成本停在拆股前 ~771）。以三大
    # 法人資料日為軸，價序列後復權（元），張序列用 share_factor 換算到現股單位（張）。
    from app.repositories.corporate_actions import load_factors
    from app.services.price_adjust import cumulative_factors

    price_acts, share_acts = await load_factors(session, symbols=[symbol])
    axis_dates = [r.data_date for r in inst_rows]
    pf = cumulative_factors(axis_dates, price_acts.get(symbol, []))
    sf = cumulative_factors(axis_dates, share_acts.get(symbol, []))

    def _padj(v: float | None, f: float) -> float | None:
        return v * f if v is not None else None

    def _sadj_lots(v: float | None, f: float) -> float | None:
        return round(v * f) if v is not None else None  # 張維持整數

    points: list[FlowPoint] = []
    inst_totals: list[float | None] = []
    foreigns: list[float | None] = []
    margin_bals: list[int | None] = []
    closes_arr: list[float | None] = []
    vols_arr: list[float | None] = []
    vwaps_arr: list[float | None] = []
    for i, r in enumerate(inst_rows):
        fp, sp = pf[i], sf[i]
        foreign = _sadj_lots(_to_lots(r.foreign_net), sp)
        trust = _sadj_lots(_to_lots(r.trust_net), sp)
        dealer_parts = [r.dealer_self_net, r.dealer_hedge_net]
        dealer = (
            _sadj_lots(_to_lots(sum(p for p in dealer_parts if p is not None)), sp)
            if any(p is not None for p in dealer_parts)
            else None
        )
        inst_total = sum(v for v in (foreign, trust, dealer) if v is not None)
        m = margin_by_date.get(r.data_date)
        mb = _sadj_lots(m.margin_balance, sp) if m and m.margin_balance is not None else None
        sb = _sadj_lots(m.short_balance, sp) if m and m.short_balance is not None else None
        close_adj = _padj(close_by_date.get(r.data_date), fp)
        points.append(
            FlowPoint(
                t=str(r.data_date),
                close=close_adj,
                foreign=foreign,
                trust=trust,
                dealer=dealer,
                inst_total=inst_total,
                margin_balance=int(mb) if mb is not None else None,
                short_balance=int(sb) if sb is not None else None,
            )
        )
        foreigns.append(foreign)
        inst_totals.append(inst_total)
        margin_bals.append(int(mb) if mb is not None else None)
        closes_arr.append(close_adj)
        vols_arr.append(_sadj_lots(vol_by_date.get(r.data_date), sp))
        vwaps_arr.append(_padj(vwap_by_date.get(r.data_date), fp))

    stock = await session.get(Stock, symbol)
    name = stock.name if stock else symbol

    tdcc_row = await load_tdcc_summary_latest(session, symbol)
    tdcc = None
    if tdcc_row is not None:
        tdcc = TdccSnapshot(
            date=str(tdcc_row.data_date),
            retail_ratio=_f(tdcc_row.retail_ratio),
            medium_ratio=_f(tdcc_row.medium_ratio),
            large_ratio=_f(tdcc_row.large_ratio),
            super_large_ratio=_f(tdcc_row.super_large_ratio),
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
    from app.services.flows import compute_cost_basis, compute_divergence

    th = get_thresholds()
    dcfg = th.divergence
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

    # 主力估算成本線（移動加權平均成本法）
    cost_basis = None
    if points:
        state_eps = float(th.cost_basis.get("state_eps", 0.01))
        cb = compute_cost_basis(vwaps_arr, inst_totals, state_eps=state_eps)
        cost_basis = CostBasis(
            points=[
                CostBasisPoint(t=p.t, cost=c) for p, c in zip(points, cb.costs)
            ],
            latest_cost=cb.latest_cost,
            latest_price=cb.latest_price,
            premium_pct=cb.premium_pct,
            state=cb.state,
            label=cb.label,
        )

    return FlowsResponse(
        symbol=symbol,
        name=name,
        count=len(points),
        days=days,
        points=points,
        tdcc=tdcc,
        summary=summary,
        divergence=divergence,
        cost_basis=cost_basis,
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
