"""里程碑 B：DB schema 與 repository roundtrip 測試。"""
from __future__ import annotations

import datetime as dt

from app.db.models.features import FeatureDaily
from app.db.models.market import DailyPrice, Stock
from app.repositories.features import FeatureDailyRepository


async def test_stock_and_price_roundtrip(db_session):
    db_session.add(Stock(symbol="2330", name="台積電", market="TWSE", industry="半導體"))
    await db_session.flush()
    db_session.add(
        DailyPrice(
            symbol="2330",
            data_date=dt.date(2026, 9, 5),
            available_at=dt.datetime(2026, 9, 5, 14, 0),
            open=1000,
            high=1010,
            low=990,
            close=1005,
            volume=30_000_000,
            turnover=30_000_000_000,
        )
    )
    await db_session.commit()

    got = await db_session.get(Stock, "2330")
    assert got is not None and got.name == "台積電"


async def test_feature_latest_and_lookahead(db_session):
    db_session.add(Stock(symbol="2330", name="台積電", market="TWSE"))
    await db_session.flush()
    for d, close in [(dt.date(2026, 9, 3), 990), (dt.date(2026, 9, 5), 1005)]:
        db_session.add(
            FeatureDaily(
                symbol="2330",
                data_date=d,
                available_at=dt.datetime(d.year, d.month, d.day, 15, 0),
                close=close,
                trust_5d_z=1.2,
            )
        )
    await db_session.commit()

    repo = FeatureDailyRepository(db_session)
    latest = await repo.get_latest("2330")
    assert latest is not None and float(latest.close) == 1005

    # look-ahead 安全：在 9/4 只能看到 9/3 的資料
    as_of = await repo.get_on_or_before("2330", dt.date(2026, 9, 4))
    assert as_of is not None and float(as_of.close) == 990
