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
    assert c.cvd_z is None and c.large_trade_delta_z is None and c.cvd_slope_norm is None


async def test_analyze_includes_intraday_when_present(db_session):
    svc = AnalysisService()

    # 有 intraday 資料（強買） vs 無 intraday（NULL）
    with_intra = FeatureDaily(
        symbol="AAA", data_date=TARGET, available_at=availability_for(TARGET),
        close=100, cvd_z=2.0, large_trade_delta_z=2.0, cvd_slope_norm=0.8,
    )
    without = FeatureDaily(
        symbol="BBB", data_date=TARGET, available_at=availability_for(TARGET),
        close=100, cvd_z=None, large_trade_delta_z=None, cvd_slope_norm=None,
    )

    r_with = svc.analyze(with_intra)
    r_without = svc.analyze(without)

    # 有逐筆 → intraday 分項脫離中性 50 且拉高 composite
    assert r_with.chip.intraday > 50
    assert r_without.chip.intraday == 50  # 排除時以中性值顯示
    assert r_with.chip.chip_score > r_without.chip.chip_score


# ---- docs/13 Phase 1：singleton 的橫斷面 z 無定義 → NULL；兩檔同值才是有效 0 ----

_Z_FIELDS = (
    "cvd_z", "large_trade_delta_z", "absorption_z", "trade_speed_z", "price_efficiency_z",
)


async def test_singleton_intraday_z_stays_null_and_excludes_component(db_session):
    await _seed_prices(db_session)
    # 當日只有 AAA 有逐筆
    for i in range(20):
        db_session.add(_tick("AAA", i, 1 if i % 5 else -1, 100 + i * 0.1))
    await db_session.commit()

    await build_features(db_session, TARGET)
    a = await FeatureDailyRepository(db_session).get_latest("AAA")

    for f in _Z_FIELDS:
        assert getattr(a, f) is None, f
    # cvd_slope_norm 是單股自身有界值，不是橫斷面 z，可保留
    assert a.cvd_slope_norm is not None
    # 不可因保留 cvd_slope_norm 而強制啟用 intraday
    assert "intraday" not in AnalysisService().score_features(a).components


async def test_two_identical_symbols_give_valid_zero_z(db_session):
    await _seed_prices(db_session)
    # AAA、BBB 逐筆完全相同（含 UNKNOWN aggressor）→ 樣本足夠且全同值 → z 為有效 0
    for i in range(20):
        side = 0 if i % 4 == 0 else (1 if i % 3 else -1)
        db_session.add(_tick("AAA", i, side, 100 + i * 0.1))
        db_session.add(_tick("BBB", i, side, 100 + i * 0.1))
    await db_session.commit()

    await build_features(db_session, TARGET)
    repo = FeatureDailyRepository(db_session)
    for sym in ("AAA", "BBB"):
        fd = await repo.get_latest(sym)
        for f in _Z_FIELDS:
            assert getattr(fd, f) == 0.0, (sym, f)
        assert "intraday" in AnalysisService().score_features(fd).components


async def test_unknown_aggressor_still_neutral_side(db_session):
    await _seed_prices(db_session)
    # 全部 UNKNOWN 的標的不得被歸為買或賣：CVD 應與「無淨主動量」一致
    for i in range(20):
        db_session.add(_tick("AAA", i, 0, 100.0))
        db_session.add(_tick("BBB", i, 1, 50 + i * 0.1))
    await db_session.commit()

    await build_features(db_session, TARGET)
    repo = FeatureDailyRepository(db_session)
    a = await repo.get_latest("AAA")
    b = await repo.get_latest("BBB")
    # 兩檔 → z 有定義；UNKNOWN 標的 cvd 相對全買方標的必為較低
    assert a.cvd_z is not None and b.cvd_z is not None
    assert a.cvd_z < b.cvd_z
