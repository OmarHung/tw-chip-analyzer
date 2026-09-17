"""大盤脈絡讀取。"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.chips import InstitutionalDaily, MarginDaily, TdccSummaryWeekly
from app.db.models.market import DailyPrice, FuturesDaily, MarketDaily, MarketIndex
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
    """只取目標日的 MarketDaily；缺當日（例如 TAIEX 抓取失敗）回 None，不沿用前一日。"""
    stmt = select(MarketDaily).where(MarketDaily.data_date == as_of)
    return (await session.execute(stmt)).scalar_one_or_none()


async def load_prev_taiex_close(
    session: AsyncSession, as_of: dt.date
) -> float | None:
    """as_of 之前最近一個交易日的 TAIEX 收盤（供算當日漲跌幅）。

    來源用 MarketIndex（每個交易日都有原始指數）而非 MarketDaily（需 MA60 視窗，
    歷史前段可能沒有）。展示用途，不依 available_at 過濾。
    """
    stmt = (
        select(MarketIndex.taiex_close)
        .where(MarketIndex.data_date < as_of, MarketIndex.taiex_close.is_not(None))
        .order_by(MarketIndex.data_date.desc())
        .limit(1)
    )
    v = (await session.execute(stmt)).scalar_one_or_none()
    return float(v) if v is not None else None


async def load_futures_front_month(
    session: AsyncSession, as_of: dt.date, contract: str = "TX"
) -> FuturesDaily | None:
    """目標日的台指期主力月份（當日日盤成交量最大者）。缺當日回 None，不沿用前一日。

    不取「到期月份最小」：結算日當天近月成交量已萎縮、報價不具代表性（見 FuturesDaily）。
    """
    stmt = (
        select(FuturesDaily)
        .where(FuturesDaily.contract == contract, FuturesDaily.data_date == as_of)
        .order_by(FuturesDaily.volume.desc().nullslast())
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def load_market_context(
    session: AsyncSession, as_of: dt.date
) -> MarketContext:
    """目標日 MarketDaily → MarketContext。無當日資料 → market_trend_score=None（未知）。

    不可取 `<= as_of` 的最新一筆：TAIEX 當日失敗時會用昨天的偏多 regime 放行今天的 BUY
    （docs/12 Phase 1）。
    """
    md = await load_market_daily(session, as_of)
    if md is None or md.market_trend_score is None:
        return MarketContext()
    return MarketContext(
        market_trend_score=float(md.market_trend_score),
        industry_trend_score=0.0,  # 產業趨勢待資料源
    )
