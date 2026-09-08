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
    SblDaily,
    TdccSummaryWeekly,
)
from app.db.models.features import FeatureDaily
from app.db.models.intraday import RawTick
from app.db.models.market import CorporateAction, DailyPrice, MarketDaily


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


async def _dates(session: AsyncSession, col) -> list[dt.date]:
    """某資料表出現過的 data_date 清單(升冪),供缺口比對。"""
    rows = (await session.execute(select(col).distinct().order_by(col))).scalars().all()
    return [d for d in rows if d is not None]


def _span_of(dates: list[dt.date]) -> dict:
    return {
        "days": len(dates),
        "min": str(dates[0]) if dates else None,
        "max": str(dates[-1]) if dates else None,
    }


def _with_gaps(dates: list[dt.date], calendar: list[dt.date]) -> dict:
    """相對交易日曆(以 daily_price 為準)算缺口:缺幾日 + 最近幾個缺漏日。

    日曆本身也可能不完整(整條鏈都沒補的那天不會出現在任何表),故這裡答的是
    「相對已知交易日還缺幾日」,不是「相對台股官方行事曆」。
    """
    have = set(dates)
    missing = [d for d in calendar if d not in have]
    return {
        **_span_of(dates),
        "missing": len(missing),
        "missing_recent": [str(d) for d in missing[-8:]],
    }


async def _row_count(session: AsyncSession, model) -> int:
    return int(
        (await session.execute(select(func.count()).select_from(model))).scalar() or 0
    )


async def load_coverage(session: AsyncSession, tick_days: int = 30) -> dict:
    """彙總各資料源涵蓋度 + 近 tick_days 天逐筆的每日檔數/筆數。"""
    # 日頻資料源:撈出實際有資料的日期,才能與交易日曆比對缺口。
    price_d = await _dates(session, DailyPrice.data_date)
    feature_d = await _dates(session, FeatureDaily.data_date)
    inst_d = await _dates(session, InstitutionalDaily.data_date)
    margin_d = await _dates(session, MarginDaily.data_date)
    sbl_d = await _dates(session, SblDaily.data_date)
    market_d = await _dates(session, MarketDaily.data_date)

    # 交易日曆基準:日線有資料的日 = 已知交易日。日線為空則退回大盤。
    calendar = price_d or market_d

    price = _with_gaps(price_d, calendar)
    feature = _with_gaps(feature_d, calendar)
    inst = _with_gaps(inst_d, calendar)
    margin = _with_gaps(margin_d, calendar)
    sbl = _with_gaps(sbl_d, calendar)
    market = _with_gaps(market_d, calendar)

    # 非日頻/非全覆蓋:週度(TDCC)、事件表(公司行動)、受配額限制(逐筆),
    # 對它們算「每個交易日都該有」沒有意義 → 不給 missing。
    tdcc = await _date_span(session, TdccSummaryWeekly.data_date)
    ca = await _date_span(session, CorporateAction.data_date)
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
            "sbl_daily": sbl,
            "market_daily": market,
            "corporate_action": ca,
            "raw_tick": tick,
        },
        # 缺口比對的基準日曆(= daily_price 有資料的交易日)。
        "calendar": _span_of(calendar),
        "row_counts": {
            "raw_tick": await _row_count(session, RawTick),
            "feature_daily": await _row_count(session, FeatureDaily),
            "daily_price": await _row_count(session, DailyPrice),
        },
        "tick_by_date": tick_rows,
    }
