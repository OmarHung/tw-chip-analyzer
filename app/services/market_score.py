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
from app.repositories.upsert import upsert_many
from app.services.normalize import clamp


async def _breadth(session: AsyncSession, target: dt.date) -> tuple[int, int]:
    """當日漲跌家數：以每檔 close 對前一交易日 close 比較。"""
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
    diff = piv[target].astype(float) - piv[prev].astype(float)
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
    ma20 = float(close_series.tail(20).mean()) if len(close_series) >= 20 else None
    ma60 = float(close_series.tail(60).mean()) if len(close_series) >= 60 else None
    # MA20 斜率：近 5 日 MA20 的變化
    slope = None
    if len(close_series) >= 25:
        ma20_series = close_series.rolling(20).mean().dropna()
        if len(ma20_series) >= 5:
            slope = float(np.polyfit(range(5), ma20_series.tail(5), 1)[0])
    # 波動：近 20 日報酬標準差
    vol = None
    if len(close_series) >= 21:
        rets = close_series.pct_change().dropna().tail(20)
        vol = float(rets.std())

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
