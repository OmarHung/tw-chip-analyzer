"""逐筆讀取順序必須可重現：同一 ts 的多筆成交依寫入序（Shioaji 原始成交序）排列。

實測（dev 2026-09-08）：只以 (symbol, ts) 排序時，同一程式連跑兩次 absorption_z /
price_efficiency_z 在 260 檔上結果不同。
"""
from __future__ import annotations

import datetime as dt

from app.db.models.intraday import RawTick
from app.db.models.market import Stock
from app.services.ticks import fetch_ticks

D = dt.date(2026, 9, 8)
TS = dt.datetime(2026, 9, 8, 9, 0, 1)


async def test_same_timestamp_ticks_keep_insertion_order(db_session):
    db_session.add(Stock(symbol="AAA", name="A", market="TWSE"))
    await db_session.flush()
    prices = [101, 99, 105, 97, 103, 100, 98, 104]
    for p in prices:
        db_session.add(RawTick(symbol="AAA", data_date=D, ts=TS, price=p, volume=1, aggressor_side=1))
        await db_session.flush()  # 逐筆 flush，id 依序遞增
    await db_session.commit()
    for _ in range(3):
        res = await fetch_ticks(db_session, "AAA", D)
        assert [t["price"] for t in res.ticks] == prices


def test_feature_builder_orders_by_id_tiebreaker():
    import inspect

    from app.services import feature_builder

    src = inspect.getsource(feature_builder._intraday_signals)
    assert "RawTick.ts, RawTick.id" in src
