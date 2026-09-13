"""逐筆懶載入的並發競態：同一檔同一日同時多個請求不可重複寫入 raw_tick。

個股頁首次開啟會同時打 /ticks 與 /orderflow：兩個 request 都看到快取未命中、各自向
Shioaji 抓取並「先刪當日再插入」。READ COMMITTED 下彼此看不到未提交的插入，
實測線上 14 組 symbol-date 被寫成 2～3 倍（CVD/成交筆數/大單量同步被放大）。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import time

from sqlalchemy import func, select

from app.db.models.intraday import RawTick
from app.db.models.market import Stock
from app.db.session import get_sessionmaker
from app.services import ticks as ticks_svc

TARGET = dt.date(2026, 9, 8)
TICKS = [
    {"ts": dt.datetime(2026, 9, 8, 9, 0, i), "price": 100.0 + i, "volume": 1,
     "bid": 99.5, "ask": 100.0, "side": 1}
    for i in range(5)
]


async def test_concurrent_first_fetch_writes_ticks_once(db_session, monkeypatch):
    db_session.add(Stock(symbol="AAA", name="A", market="TWSE"))
    await db_session.commit()

    calls: list[int] = []

    def slow_fetch(symbol, date):
        calls.append(1)
        time.sleep(0.3)  # 模擬 Shioaji 網路延遲，讓兩個請求的抓取重疊
        return TICKS

    monkeypatch.setattr(ticks_svc, "fetch_ticks_sync", slow_fetch)

    maker = get_sessionmaker()

    async def one_request():
        async with maker() as s:
            return await ticks_svc.fetch_ticks(s, "AAA", TARGET)

    results = await asyncio.gather(one_request(), one_request(), one_request())

    n = (await db_session.execute(
        select(func.count()).select_from(RawTick)
        .where(RawTick.symbol == "AAA", RawTick.data_date == TARGET)
    )).scalar_one()
    assert n == len(TICKS)
    # 每個請求都拿到完整且不重複的逐筆
    assert all(len(r.ticks) == len(TICKS) for r in results)
    # 後到者等鎖後直接讀快取，不重複消耗 Shioaji 配額
    assert len(calls) == 1
