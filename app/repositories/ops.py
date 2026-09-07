"""系統狀態:資料涵蓋度查詢(供 /api/ops/status)。

純唯讀彙總:各資料源的交易日數/日期範圍/列數,以及逐筆(raw_tick)近期每日
灌了幾檔——用來一眼看出「哪天逐筆被 Shioaji 配額砍到只剩幾檔」這類問題。
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.chips import (
    InstitutionalDaily,
    MarginDaily,
    TdccSummaryWeekly,
)
from app.db.models.features import FeatureDaily
from app.db.models.intraday import RawTick
from app.db.models.market import DailyPrice, MarketDaily


async def _date_span(session: AsyncSession, col) -> dict:
    """某資料表的 data_date 涵蓋:交易日數(distinct)、最早、最新。"""
    stmt = select(
        func.count(func.distinct(col)),
        func.min(col),
        func.max(col),
    )
    days, dmin, dmax = (await session.execute(stmt)).one()
    return {
        "days": int(days or 0),
        "min": str(dmin) if dmin else None,
        "max": str(dmax) if dmax else None,
    }


async def _row_count(session: AsyncSession, model) -> int:
    return int(
        (await session.execute(select(func.count()).select_from(model))).scalar() or 0
    )


async def load_coverage(session: AsyncSession, tick_days: int = 30) -> dict:
    """彙總各資料源涵蓋度 + 近 tick_days 天逐筆的每日檔數/筆數。"""
    feature = await _date_span(session, FeatureDaily.data_date)
    price = await _date_span(session, DailyPrice.data_date)
    inst = await _date_span(session, InstitutionalDaily.data_date)
    margin = await _date_span(session, MarginDaily.data_date)
    tdcc = await _date_span(session, TdccSummaryWeekly.data_date)
    market = await _date_span(session, MarketDaily.data_date)
    tick = await _date_span(session, RawTick.data_date)

    # 逐筆:近 N 天每日灌了幾檔(distinct symbol)與總筆數
    tick_stmt = (
        select(
            RawTick.data_date,
            func.count(func.distinct(RawTick.symbol)),
            func.count(),
        )
        .group_by(RawTick.data_date)
        .order_by(RawTick.data_date.desc())
        .limit(tick_days)
    )
    tick_rows = [
        {"date": str(d), "symbols": int(s), "ticks": int(n)}
        for d, s, n in (await session.execute(tick_stmt)).all()
    ]

    return {
        "sources": {
            "feature_daily": feature,
            "daily_price": price,
            "institutional_daily": inst,
            "margin_daily": margin,
            "tdcc_summary_weekly": tdcc,
            "market_daily": market,
            "raw_tick": tick,
        },
        "row_counts": {
            "raw_tick": await _row_count(session, RawTick),
            "feature_daily": await _row_count(session, FeatureDaily),
            "daily_price": await _row_count(session, DailyPrice),
        },
        "tick_by_date": tick_rows,
    }
