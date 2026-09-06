"""大盤脈絡讀取。"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.market import MarketDaily
from app.models.signal import MarketContext


async def load_market_daily(session: AsyncSession, as_of: dt.date) -> MarketDaily | None:
    stmt = (
        select(MarketDaily)
        .where(MarketDaily.data_date <= as_of)
        .order_by(MarketDaily.data_date.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def load_market_context(
    session: AsyncSession, as_of: dt.date
) -> MarketContext:
    """取 data_date<=as_of 的最新 MarketDaily → MarketContext。無資料則中性。"""
    stmt = (
        select(MarketDaily)
        .where(MarketDaily.data_date <= as_of)
        .order_by(MarketDaily.data_date.desc())
        .limit(1)
    )
    md = (await session.execute(stmt)).scalar_one_or_none()
    if md is None or md.market_trend_score is None:
        return MarketContext()
    return MarketContext(
        market_trend_score=float(md.market_trend_score),
        industry_trend_score=0.0,  # 產業趨勢待資料源
    )
