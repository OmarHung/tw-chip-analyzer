"""大盤 regime 計算（見 docs/03 §10）。

由 TAIEX 日線（MA20/MA60/斜率/波動）+ 全市場漲跌家數，
組合成 market_trend_score ∈ -1..1，寫入 market_daily。
"""
from __future__ import annotations

import datetime as dt
from math import tanh

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Thresholds, get_thresholds
from app.db.models.market import DailyPrice, MarketDaily, MarketIndex
from app.importers.base import availability_for
from app.repositories.corporate_actions import load_factors
from app.repositories.upsert import upsert_many
from app.services.normalize import clamp


async def _breadth(session: AsyncSession, target: dt.date) -> tuple[int, int]:
    """當日漲跌家數：以每檔 close 對「還原後」前一交易日 close 比較。

    除息/配股/減資在 target 當日造成的機械價差不是漲跌（docs/09 BUG-12）：前收乘上
    (prev, target] 之間的價格因子，等同交易所的參考價。
    """
    prev_stmt = (
        select(DailyPrice.data_date)
        .where(DailyPrice.data_date < target)
        .order_by(DailyPrice.data_date.desc())
        .limit(1)
    )
    prev = (await session.execute(prev_stmt)).scalar_one_or_none()
    if prev is None:
        return 0, 0

    rows = (
        await session.execute(
            select(DailyPrice.symbol, DailyPrice.data_date, DailyPrice.close).where(
                DailyPrice.data_date.in_([prev, target])
            )
        )
    ).all()
    df = pd.DataFrame(rows, columns=["symbol", "data_date", "close"])
    piv = df.pivot_table(index="symbol", columns="data_date", values="close")
    if prev not in piv or target not in piv:
        return 0, 0
    prev_close = piv[prev].astype(float)
    price_factors, _ = await load_factors(
        session, start=prev + dt.timedelta(days=1), end=target
    )
    for sym, acts in price_factors.items():
        if sym in prev_close.index:
            for _, factor in acts:
                prev_close[sym] *= factor
    diff = piv[target].astype(float) - prev_close
    return int((diff > 0).sum()), int((diff < 0).sum())


def _trend_score(
    close: float, ma20: float | None, ma60: float | None, slope: float | None,
    breadth: float, vol: float | None, t: Thresholds,
) -> float:
    w = t.market_regime
    score = 0.0
    if ma20 is not None:
        score += w["above_ma20"] * (1 if close > ma20 else -1)
    if ma60 is not None:
        score += w["above_ma60"] * (1 if close > ma60 else -1)
    if slope is not None and ma20:
        score += w["ma20_slope"] * tanh(slope / ma20 * 60 / w["slope_scale"] * 0.01)
    score += w["breadth"] * breadth
    if vol is not None:
        # 高波動 regime → 偏空調整
        score += w["volatility"] * (-1 if vol > w["high_vol_pct"] else 0.5)
    return clamp(score)


def _regime_inputs(
    close_series: pd.Series, t: Thresholds
) -> tuple[float | None, float | None, float | None, float | None]:
    """TAIEX 收盤序列 → (短均線, 長均線, 短均線斜率, 報酬波動)。視窗皆讀 market_regime.*。

    bar 數不足回 None（不以較短資料冒充）。預設值與修正前相同（20/60/5/20）。
    """
    w = t.market_regime
    short_n = int(w.get("ma_short_bars", 20))
    long_n = int(w.get("ma_long_bars", 60))
    slope_n = int(w.get("slope_lookback_bars", 5))
    vol_n = int(w.get("volatility_bars", 20))
    n = len(close_series)
    ma_short = float(close_series.tail(short_n).mean()) if n >= short_n else None
    ma_long = float(close_series.tail(long_n).mean()) if n >= long_n else None
    slope = None
    if n >= short_n + slope_n:
        ma_series = close_series.rolling(short_n).mean().dropna()
        if len(ma_series) >= slope_n:
            slope = float(np.polyfit(range(slope_n), ma_series.tail(slope_n), 1)[0])
    vol = None
    if n >= vol_n + 1:
        vol = float(close_series.pct_change().dropna().tail(vol_n).std())
    return ma_short, ma_long, slope, vol


async def build_market_daily(
    session: AsyncSession, target: dt.date, thresholds: Thresholds | None = None
) -> bool:
    t = thresholds or get_thresholds()

    idx_rows = (
        await session.execute(
            select(MarketIndex.data_date, MarketIndex.taiex_close)
            .where(MarketIndex.data_date <= target)
            .order_by(MarketIndex.data_date)
        )
    ).all()
    if not idx_rows:
        return False
    idx = pd.DataFrame(idx_rows, columns=["data_date", "taiex_close"]).dropna()
    if idx.empty or idx["data_date"].iloc[-1] != target:
        return False

    close_series = idx["taiex_close"].astype(float)
    close = float(close_series.iloc[-1])
    ma20, ma60, slope, vol = _regime_inputs(close_series, t)

    adv, dec = await _breadth(session, target)
    breadth = (adv - dec) / (adv + dec) if (adv + dec) > 0 else 0.0

    trend = _trend_score(close, ma20, ma60, slope, breadth, vol, t)

    row = {
        "data_date": target,
        "available_at": availability_for(target),
        "taiex_close": round(close, 2),
        "taiex_ma20": round(ma20, 2) if ma20 is not None else None,
        "taiex_ma60": round(ma60, 2) if ma60 is not None else None,
        "ma20_slope": slope,
        "advancers": adv,
        "decliners": dec,
        "volatility_pct": vol,
        "market_trend_score": trend,
    }
    await upsert_many(session, MarketDaily, [row], ["data_date"])
    await session.commit()
    return True
