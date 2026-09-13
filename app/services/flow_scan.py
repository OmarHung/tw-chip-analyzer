"""全市場背離掃描：對最新交易日,逐股算 60 日量價背離狀態,供選出主力低接/出貨候選。

背離訊號已於 scripts/divergence_backtest.py 驗證(60日窗方向正確、隨持有期擴大)。
此處以「當前」全部可得資料(data_date<=最新)算最新狀態,供選股。
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_thresholds
from app.repositories.corporate_actions import load_factors
from app.services.price_adjust import cumulative_factors
from app.db.models.chips import InstitutionalDaily
from app.db.models.market import DailyPrice, Stock
from app.services.flows import compute_cost_basis, compute_divergence


@dataclass
class DivergenceScanRow:
    symbol: str
    name: str
    price: float | None
    change_pct: float | None
    turnover: float
    status: str
    label: str
    window: int
    price_return: float | None
    flow_ratio: float | None  # 淨買超佔區間成交比重
    inst_net: float  # 區間三大法人淨買超（張）
    cost_state: str
    premium_pct: float | None  # 現價 vs 主力估算成本
    mom_pct: float | None = None  # 60 日動能橫斷面百分位(0~100);研究訊號(60 日反轉),不計入分數


_LABELS = {
    "bullish_div": "正背離",
    "bearish_div": "負背離",
    "aligned_up": "同向偏多",
    "aligned_down": "同向偏空",
    "neutral": "中性",
}

# (as_of, window, lookback_days) -> rows 快取（EOD 每日更新，當日內重用）。
# key 必須含視窗參數，否則先查 60 日再查 20 日會拿到 60 日結果（docs/09 BUG-06）。
_cache: dict[tuple[dt.date, int, int], list[DivergenceScanRow]] = {}


async def scan_divergence(
    session: AsyncSession, *, window: int = 60, lookback_days: int = 150
) -> tuple[dt.date | None, list[DivergenceScanRow]]:
    latest = (
        await session.execute(
            select(DailyPrice.data_date).order_by(DailyPrice.data_date.desc()).limit(1)
        )
    ).scalar_one_or_none()
    if latest is None:
        return None, []
    key = (latest, window, lookback_days)
    if key in _cache:
        return latest, _cache[key]

    start = latest - dt.timedelta(days=lookback_days)
    th = get_thresholds()
    dcfg = th.divergence
    price_eps = float(dcfg.get("price_eps", 0.03))
    flow_eps = float(dcfg.get("flow_eps", 0.02))
    min_points = int(dcfg.get("min_points", 10))
    state_eps = float(th.cost_basis.get("state_eps", 0.01))

    # 一次載入範圍內全市場價量與法人買賣超
    prows = (
        await session.execute(
            select(DailyPrice)
            .where(DailyPrice.data_date >= start)
            .order_by(DailyPrice.symbol, DailyPrice.data_date)
        )
    ).scalars()
    closes: dict[str, list[float | None]] = defaultdict(list)
    vwaps: dict[str, list[float | None]] = defaultdict(list)
    vols: dict[str, list[float | None]] = defaultdict(list)
    dates: dict[str, list[dt.date]] = defaultdict(list)
    turnover_latest: dict[str, float] = {}
    for r in prows:
        c = float(r.close) if r.close is not None else None
        closes[r.symbol].append(c)
        vols[r.symbol].append(r.volume / 1000.0 if r.volume else None)
        if r.turnover and r.volume:
            vwaps[r.symbol].append(float(r.turnover) / r.volume)
        else:
            vwaps[r.symbol].append(c)
        dates[r.symbol].append(r.data_date)
        turnover_latest[r.symbol] = float(r.turnover or 0)

    irows = (
        await session.execute(
            select(InstitutionalDaily)
            .where(InstitutionalDaily.data_date >= start)
            .order_by(InstitutionalDaily.symbol, InstitutionalDaily.data_date)
        )
    ).scalars()
    inst_by_date: dict[str, dict[dt.date, float]] = defaultdict(dict)
    for r in irows:
        parts = [r.foreign_net, r.trust_net, r.dealer_self_net, r.dealer_hedge_net]
        inst_by_date[r.symbol][r.data_date] = sum(p for p in parts if p is not None) / 1000.0

    names = dict(
        (s, n)
        for s, n in (
            await session.execute(select(Stock.symbol, Stock.name))
        ).all()
    )

    # 公司行動還原（與個股 /flows 同一套因子）：價/VWAP 乘價格因子、量/法人張數乘股數因子，
    # 拆股/減資日才不會出現假的量價背離（docs/09 BUG-14）。
    price_acts, share_acts = await load_factors(session, end=latest)

    def _scaled(vals: list[float | None], factors: list[float]) -> list[float | None]:
        return [v * f if v is not None else None for v, f in zip(vals, factors)]

    rows: list[DivergenceScanRow] = []
    for sym, ds in dates.items():
        # 只納入最新交易日有行情者：停牌/下市/缺資料股不可帶著舊價格出現在今日掃描
        if ds[-1] != latest:
            continue
        pf = cumulative_factors(ds, price_acts.get(sym, []))
        sf = cumulative_factors(ds, share_acts.get(sym, []))
        closes[sym] = _scaled(closes[sym], pf)
        vwaps[sym] = _scaled(vwaps[sym], pf)
        vols[sym] = _scaled(vols[sym], sf)
        inst_arr = _scaled([inst_by_date.get(sym, {}).get(d) for d in ds], sf)
        div = compute_divergence(
            closes[sym], inst_arr, vols[sym],
            window=window, price_eps=price_eps, flow_eps=flow_eps, min_points=min_points,
        )
        if div is None:
            continue
        cb = compute_cost_basis(vwaps[sym], inst_arr, state_eps=state_eps)
        cs = closes[sym]
        price = next((x for x in reversed(cs) if x is not None), None)
        prev = None
        seen = False
        for x in reversed(cs):
            if x is None:
                continue
            if not seen:
                seen = True
                continue
            prev = x
            break
        change = (price / prev - 1.0) if (price and prev) else None
        rows.append(
            DivergenceScanRow(
                symbol=sym,
                name=names.get(sym, sym),
                price=price,
                change_pct=change,
                turnover=turnover_latest.get(sym, 0.0),
                status=div.status,
                label=_LABELS[div.status],
                window=window,
                price_return=div.price_return,
                flow_ratio=div.inst_flow_ratio,
                inst_net=div.inst_net,
                cost_state=cb.state,
                premium_pct=cb.premium_pct,
            )
        )
    # 60 日動能(= price_return)橫斷面百分位:低=跌深(反彈候選)、高=漲多(回落候選)。
    # 研究訊號,對應 mom_60d 的 OOS 檢驗(單一 regime,未計入 Chip Score)。
    import bisect

    prs = sorted(r.price_return for r in rows if r.price_return is not None)
    if prs:
        for r in rows:
            if r.price_return is not None:
                k = bisect.bisect_right(prs, r.price_return)
                r.mom_pct = round(k / len(prs) * 100, 1)

    if any(k[0] != latest for k in _cache):
        _cache.clear()  # 新交易日 → 舊日結果作廢，避免快取無限成長
    _cache[key] = rows
    return latest, rows
