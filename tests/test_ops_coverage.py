"""資料涵蓋度統計（app.repositories.ops.load_coverage）。

重點是 raw_tick 的專用路徑：它是唯一的巨表，統計不得對它做全表聚合
（2026-09-15 首頁 504 事故的根因）。這裡驗證改寫後語意不變。
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from app.db.models.intraday import RawTick
from app.db.models.market import DailyPrice, Stock
from app.repositories.ops import load_coverage

pytestmark = pytest.mark.asyncio


async def _seed_stock(session, symbol: str = "2330") -> None:
    session.add(Stock(symbol=symbol, name="台積電", market="TWSE"))
    await session.flush()


def _tick(symbol: str, day: dt.date, seq: int) -> RawTick:
    return RawTick(
        symbol=symbol,
        data_date=day,
        ts=dt.datetime.combine(day, dt.time(9, 0)) + dt.timedelta(seconds=seq),
        price=Decimal("100.0"),
        volume=1,
        aggressor_side=1,
    )


async def test_tick_span_counts_distinct_dates(db_session) -> None:
    # Arrange：三個相異日期，每日多筆
    await _seed_stock(db_session)
    days = [dt.date(2026, 9, 1), dt.date(2026, 9, 2), dt.date(2026, 9, 3)]
    for d in days:
        for seq in range(3):
            db_session.add(_tick("2330", d, seq))
    await db_session.flush()

    # Act
    cov = await load_coverage(db_session)

    # Assert
    assert cov["sources"]["raw_tick"] == {
        "days": 3,
        "min": "2026-09-01",
        "max": "2026-09-03",
    }
    assert cov["row_counts"]["raw_tick"] == 9


async def test_tick_by_date_limited_to_recent_days(db_session) -> None:
    # Arrange：5 個交易日的逐筆
    await _seed_stock(db_session)
    days = [dt.date(2026, 9, d) for d in (1, 2, 3, 4, 5)]
    for d in days:
        db_session.add(_tick("2330", d, 0))
    await db_session.flush()

    # Act：只要最近 2 天
    cov = await load_coverage(db_session, tick_days=2)

    # Assert：span 仍涵蓋全部，逐日明細只給最近 2 天（新到舊）
    assert cov["sources"]["raw_tick"]["days"] == 5
    assert [r["date"] for r in cov["tick_by_date"]] == ["2026-09-05", "2026-09-04"]
    assert cov["tick_by_date"][0] == {"date": "2026-09-05", "symbols": 1, "ticks": 1}


async def test_empty_raw_tick_gives_zero_span(db_session) -> None:
    # Arrange：完全沒有逐筆
    await _seed_stock(db_session)

    # Act
    cov = await load_coverage(db_session)

    # Assert
    assert cov["sources"]["raw_tick"] == {"days": 0, "min": None, "max": None}
    assert cov["tick_by_date"] == []
    assert cov["row_counts"]["raw_tick"] == 0


async def test_row_counts_estimated_flags_unanalyzed_table(db_session) -> None:
    # Arrange：測試庫剛建表、從未 ANALYZE → reltuples 無效，應退回精確 count
    await _seed_stock(db_session)
    db_session.add(_tick("2330", dt.date(2026, 9, 1), 0))
    await db_session.flush()

    # Act
    cov = await load_coverage(db_session)

    # Assert：精確值時不得標記為估計
    assert cov["row_counts"]["raw_tick"] == 1
    assert cov["row_counts_estimated"] == []


async def test_daily_price_gaps_still_reported(db_session) -> None:
    # Arrange：日線缺 9/2（回歸測試：raw_tick 改寫不得影響日頻來源的缺口偵測）
    await _seed_stock(db_session)
    for d in (dt.date(2026, 9, 1), dt.date(2026, 9, 3)):
        db_session.add(
            DailyPrice(
                symbol="2330",
                data_date=d,
                available_at=dt.datetime.combine(d, dt.time(18, 0)),
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100"),
                volume=1000,
            )
        )
    await db_session.flush()

    # Act
    cov = await load_coverage(db_session)

    # Assert
    assert cov["sources"]["daily_price"]["days"] == 2
    assert cov["calendar"]["min"] == "2026-09-01"
