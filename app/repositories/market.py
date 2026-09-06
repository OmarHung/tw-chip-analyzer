"""大盤脈絡讀取。"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.chips import InstitutionalDaily, MarginDaily, TdccSummaryWeekly
from app.db.models.market import DailyPrice, MarketDaily
from app.models.signal import MarketContext


async def load_daily_prices(
    session: AsyncSession, symbol: str, limit: int = 250
) -> list[DailyPrice]:
    """取某檔近 limit 個交易日的日 K(升冪),供走勢圖自家資料來源。

    走勢圖只做展示,不涉回測,故取全部歷史(不依 available_at 過濾)。
    """
    stmt = (
        select(DailyPrice)
        .where(DailyPrice.symbol == symbol)
        .order_by(DailyPrice.data_date.desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return list(reversed(rows))


async def load_institutional_since(
    session: AsyncSession, symbol: str, start: dt.date
) -> list[InstitutionalDaily]:
    """某檔 data_date>=start 的三大法人買賣超(升冪)。展示用,不依 available_at 過濾。"""
    stmt = (
        select(InstitutionalDaily)
        .where(InstitutionalDaily.symbol == symbol, InstitutionalDaily.data_date >= start)
        .order_by(InstitutionalDaily.data_date)
    )
    return list((await session.execute(stmt)).scalars().all())


async def load_margin_since(
    session: AsyncSession, symbol: str, start: dt.date
) -> list[MarginDaily]:
    """某檔 data_date>=start 的融資融券(升冪)。"""
    stmt = (
        select(MarginDaily)
        .where(MarginDaily.symbol == symbol, MarginDaily.data_date >= start)
        .order_by(MarginDaily.data_date)
    )
    return list((await session.execute(stmt)).scalars().all())


async def load_prices_since(
    session: AsyncSession, symbol: str, start: dt.date
) -> list[DailyPrice]:
    """某檔 data_date>=start 的日 K(升冪),供主力進出圖疊價格。"""
    stmt = (
        select(DailyPrice)
        .where(DailyPrice.symbol == symbol, DailyPrice.data_date >= start)
        .order_by(DailyPrice.data_date)
    )
    return list((await session.execute(stmt)).scalars().all())


async def load_tdcc_summary_latest(
    session: AsyncSession, symbol: str
) -> TdccSummaryWeekly | None:
    """某檔最新一週 TDCC 大戶/散戶聚合(單點快照)。"""
    stmt = (
        select(TdccSummaryWeekly)
        .where(TdccSummaryWeekly.symbol == symbol)
        .order_by(TdccSummaryWeekly.data_date.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


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
