"""signal_persist.persist_signals 落地測試（供 backtest / ML 的單一真相來源）。

驗證:落地筆數、available_at(look-ahead 安全)、payload 為 feature 快照、
upsert 冪等、無 feature 當日回 0。
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select

from app.db.models.chips import InstitutionalDaily, MarginDaily
from app.db.models.features import SignalSnapshot
from app.db.models.market import DailyPrice, Stock
from app.importers.base import availability_for
from app.services.feature_builder import build_features
from app.services.signal_persist import persist_signals

TARGET = dt.date(2026, 3, 20)
_ACTIONS = {"BUY", "WATCH", "HOLD", "REDUCE", "EXIT", "AVOID"}


async def _seed_history(session):
    """三檔、25 個交易日;A 強買、C 強賣(足夠 5/20 視窗)。"""
    session.add_all(
        [
            Stock(symbol="AAA", name="A", market="TWSE", industry="半導體"),
            Stock(symbol="BBB", name="B", market="TWSE", industry="金融"),
            Stock(symbol="CCC", name="C", market="TWSE", industry="航運"),
        ]
    )
    await session.flush()
    specs = {"AAA": 100.0, "BBB": 50.0, "CCC": 200.0}
    inst = {"AAA": 3_000_000, "BBB": 0, "CCC": -3_000_000}
    for i in range(25):
        d = TARGET - dt.timedelta(days=24 - i)
        av = availability_for(d)
        for sym, base in specs.items():
            px = base + i * 0.5
            session.add(DailyPrice(
                symbol=sym, data_date=d, available_at=av,
                open=px, high=px * 1.01, low=px * 0.99, close=px,
                volume=1_000_000, turnover=px * 1_000_000,
            ))
            session.add(InstitutionalDaily(
                symbol=sym, data_date=d, available_at=av,
                foreign_net=inst[sym], trust_net=inst[sym] // 2,
                dealer_self_net=0, dealer_hedge_net=0,
            ))
            session.add(MarginDaily(
                symbol=sym, data_date=d, available_at=av,
                margin_balance=10_000 + i * (10 if sym == "CCC" else -5),
                short_balance=1_000,
            ))
    await session.commit()


async def test_persist_signals_lands_rows(db_session):
    await _seed_history(db_session)
    await build_features(db_session, TARGET)
    n = await persist_signals(db_session, TARGET)
    assert n == 3

    rows = (await db_session.execute(
        select(SignalSnapshot).where(SignalSnapshot.data_date == TARGET)
    )).scalars().all()
    assert {r.symbol for r in rows} == {"AAA", "BBB", "CCC"}
    for r in rows:
        # look-ahead 安全:available_at 為當日盤後(DB 回傳帶 tz,比 wall-clock)
        assert r.available_at.replace(tzinfo=None) == availability_for(TARGET)
        assert r.chip_score is not None
        assert r.action in _ACTIONS
        # payload 為 feature 快照:含特徵欄、排除識別/時間戳
        assert isinstance(r.payload, dict)
        assert "foreign_5d_z" in r.payload
        assert "id" not in r.payload and "symbol" not in r.payload
        assert "available_at" not in r.payload


async def test_persist_signals_idempotent(db_session):
    await _seed_history(db_session)
    await build_features(db_session, TARGET)
    await persist_signals(db_session, TARGET)
    await persist_signals(db_session, TARGET)  # 重跑不應重複
    total = (await db_session.execute(
        select(func.count()).select_from(SignalSnapshot)
        .where(SignalSnapshot.data_date == TARGET)
    )).scalar_one()
    assert total == 3


async def test_persist_signals_empty_day_returns_zero(db_session):
    # 無 feature_daily 的日期:不落地、回 0
    n = await persist_signals(db_session, dt.date(2020, 1, 2))
    assert n == 0
