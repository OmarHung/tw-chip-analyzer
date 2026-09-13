"""rebuild_signals 的 min_lookback 必須以「全部交易日曆」計算，而非指定區間內。

實際踩到（2026-09-13 線上）：只重建 09-07～09-11（5 天），舊版在區間內再跳過前 20 天
→ 一天都沒重建，工作卻顯示成功。回看視窗是否足夠取決於資料起點，與重建區間無關。
"""
from __future__ import annotations

import datetime as dt

from app.db.models.market import DailyPrice, Stock
from app.importers.base import availability_for
from scripts import rebuild_signals

START = dt.date(2026, 1, 1)
DAYS = [START + dt.timedelta(days=i) for i in range(30)]


async def _seed(session):
    session.add(Stock(symbol="AAA", name="A", market="TWSE"))
    await session.flush()
    for d in DAYS:
        session.add(DailyPrice(symbol="AAA", data_date=d, available_at=availability_for(d),
                               open=10, high=10, low=10, close=10, volume=1, turnover=10))
    await session.commit()


def _record(monkeypatch) -> list[dt.date]:
    done: list[dt.date] = []

    async def fake_rebuild_day(t):
        done.append(t)
        return 0

    monkeypatch.setattr(rebuild_signals, "rebuild_day", fake_rebuild_day)
    return done


async def test_short_range_after_lookback_is_rebuilt(db_session, monkeypatch):
    await _seed(db_session)
    done = _record(monkeypatch)

    await rebuild_signals.rebuild(DAYS[25], DAYS[29], min_lookback=20)

    assert done == DAYS[25:30]


async def test_range_overlapping_lookback_skips_only_early_days(db_session, monkeypatch):
    await _seed(db_session)
    done = _record(monkeypatch)

    await rebuild_signals.rebuild(DAYS[15], DAYS[24], min_lookback=20)

    assert done == DAYS[20:25]


async def test_full_rebuild_unchanged(db_session, monkeypatch):
    await _seed(db_session)
    done = _record(monkeypatch)

    await rebuild_signals.rebuild(None, None, min_lookback=20)

    assert done == DAYS[20:]
