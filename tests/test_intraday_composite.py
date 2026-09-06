"""盤中 intraday 併入全市場評分：橫斷面 z + composite 動態納入。"""
from __future__ import annotations

import datetime as dt

from app.db.models.features import FeatureDaily
from app.db.models.intraday import RawTick
from app.db.models.market import DailyPrice, Stock
from app.importers.base import availability_for
from app.repositories.features import FeatureDailyRepository
from app.services.analysis import AnalysisService
from app.services.feature_builder import build_features

TARGET = dt.date(2026, 3, 20)


async def _seed_prices(session):
    session.add_all(
        [
            Stock(symbol="AAA", name="A", market="TWSE"),
            Stock(symbol="BBB", name="B", market="TWSE"),
            Stock(symbol="CCC", name="C", market="TWSE"),  # 無逐筆
        ]
    )
    await session.flush()
    av = availability_for(TARGET)
    for sym, px in {"AAA": 100.0, "BBB": 50.0, "CCC": 30.0}.items():
        session.add(
            DailyPrice(
                symbol=sym, data_date=TARGET, available_at=av,
                open=px, high=px * 1.02, low=px * 0.98, close=px,
                volume=1_000_000, turnover=px * 1_000_000,
            )
        )


def _tick(sym, i, side, price):
    # 以 target 當日盤中時間排列（naive 台北牆鐘）
    ts = dt.datetime(2026, 3, 20, 9, 0, 0) + dt.timedelta(seconds=i * 30)
    return RawTick(
        symbol=sym, data_date=TARGET, ts=ts,
        price=price, volume=10, bid_price=price - 0.1,
        ask_price=price + 0.1, aggressor_side=side,
    )


async def test_intraday_cross_sectional_z_and_null(db_session):
    await _seed_prices(db_session)
    # AAA 主動買為主、BBB 主動賣為主；CCC 無逐筆
    for i in range(20):
        db_session.add(_tick("AAA", i, 1 if i % 5 else -1, 100 + i * 0.1))
        db_session.add(_tick("BBB", i, -1 if i % 5 else 1, 50 - i * 0.1))
    await db_session.commit()

    n = await build_features(db_session, TARGET)
    assert n == 3

    repo = FeatureDailyRepository(db_session)
    a = await repo.get_latest("AAA")
    b = await repo.get_latest("BBB")
    c = await repo.get_latest("CCC")

    # 有逐筆的標的 intraday 欄位有值；買方 cvd_z 應高於賣方
    assert a.cvd_z is not None and b.cvd_z is not None
    assert a.cvd_z > b.cvd_z
    assert a.cvd_z > 0 > b.cvd_z
    # 無逐筆的標的維持 NULL
    assert c.cvd_z is None and c.large_trade_delta_z is None and c.intraday_obi is None


async def test_analyze_includes_intraday_when_present(db_session):
    svc = AnalysisService()

    # 有 intraday 資料（強買） vs 無 intraday（NULL）
    with_intra = FeatureDaily(
        symbol="AAA", data_date=TARGET, available_at=availability_for(TARGET),
        close=100, cvd_z=2.0, large_trade_delta_z=2.0, intraday_obi=0.8,
    )
    without = FeatureDaily(
        symbol="BBB", data_date=TARGET, available_at=availability_for(TARGET),
        close=100, cvd_z=None, large_trade_delta_z=None, intraday_obi=None,
    )

    r_with = svc.analyze(with_intra)
    r_without = svc.analyze(without)

    # 有逐筆 → intraday 分項脫離中性 50 且拉高 composite
    assert r_with.chip.intraday > 50
    assert r_without.chip.intraday == 50  # 排除時以中性值顯示
    assert r_with.chip.chip_score > r_without.chip.chip_score
