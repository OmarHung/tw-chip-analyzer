"""Feature builder 測試（見 docs/03 §9、docs/06 §18）。"""
from __future__ import annotations

import datetime as dt

from app.db.models.chips import InstitutionalDaily, MarginDaily
from app.db.models.market import DailyPrice, Stock
from app.importers.base import availability_for
from app.repositories.features import FeatureDailyRepository
from app.services.feature_builder import build_features

TARGET = dt.date(2026, 3, 20)


async def _seed_history(session):
    """三檔股票、25 個交易日；A 法人強力買超、C 明顯賣超。"""
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
            session.add(
                DailyPrice(
                    symbol=sym, data_date=d, available_at=av,
                    open=px, high=px * 1.01, low=px * 0.99, close=px,
                    volume=1_000_000, turnover=px * 1_000_000,
                )
            )
            session.add(
                InstitutionalDaily(
                    symbol=sym, data_date=d, available_at=av,
                    foreign_net=inst[sym], trust_net=inst[sym] // 2,
                    dealer_self_net=0, dealer_hedge_net=0,
                )
            )
            session.add(
                MarginDaily(
                    symbol=sym, data_date=d, available_at=av,
                    margin_balance=10_000 + i * (10 if sym == "CCC" else -5),
                    short_balance=1_000,
                )
            )
    await session.commit()


async def test_build_features_basic(db_session):
    await _seed_history(db_session)
    n = await build_features(db_session, TARGET)
    assert n == 3

    repo = FeatureDailyRepository(db_session)
    a = await repo.get_latest("AAA")
    assert a is not None
    assert a.close is not None and a.atr14 is not None and a.ma20 is not None
    assert a.vwap is not None and a.recent_swing_low is not None


async def test_cross_sectional_zscore_ranks_buyers_high(db_session):
    await _seed_history(db_session)
    await build_features(db_session, TARGET)
    repo = FeatureDailyRepository(db_session)
    a = await repo.get_latest("AAA")  # 強買
    c = await repo.get_latest("CCC")  # 強賣
    # 買超股 foreign z 應顯著高於賣超股
    assert a.foreign_5d_z > c.foreign_5d_z
    assert a.foreign_5d_z > 0 > c.foreign_5d_z


async def test_lookahead_only_uses_past(db_session):
    await _seed_history(db_session)
    # 加入未來一天的價格（不應被 target 當日的特徵使用）
    future = TARGET + dt.timedelta(days=1)
    db_session.add(
        DailyPrice(
            symbol="AAA", data_date=future, available_at=availability_for(future),
            open=999, high=999, low=999, close=999, volume=1, turnover=999,
        )
    )
    await db_session.commit()

    await build_features(db_session, TARGET)
    a = await FeatureDailyRepository(db_session).get_latest("AAA")
    # target 當日 close 不應等於未來的 999
    assert a.data_date == TARGET
    assert float(a.close) != 999


async def _seed_industries(session):
    """兩個產業各 6 檔：半導體整體走強、航運整體走弱（超過成分股門檻 5）。"""
    groups = {"半導體": 0.008, "航運": -0.006}
    for ind, drift in groups.items():
        for k in range(6):
            sym = f"{'S' if ind == '半導體' else 'M'}{k:02d}"
            session.add(Stock(symbol=sym, name=sym, market="TWSE", industry=ind))
    await session.flush()
    for ind, drift in groups.items():
        for k in range(6):
            sym = f"{'S' if ind == '半導體' else 'M'}{k:02d}"
            px = 100.0
            for i in range(25):
                d = TARGET - dt.timedelta(days=24 - i)
                px *= 1 + drift
                session.add(
                    DailyPrice(
                        symbol=sym, data_date=d, available_at=availability_for(d),
                        open=px, high=px * 1.01, low=px * 0.99, close=px,
                        volume=1_000_000, turnover=px * 1_000_000,
                    )
                )
    await session.commit()


async def test_industry_trend_ranks_strong_industry_higher(db_session):
    await _seed_industries(db_session)
    await build_features(db_session, TARGET)
    repo = FeatureDailyRepository(db_session)
    s = await repo.get_latest("S00")  # 半導體（走強）
    m = await repo.get_latest("M00")  # 航運（走弱）
    assert s.industry_trend_score is not None and m.industry_trend_score is not None
    assert s.industry_trend_score > m.industry_trend_score
    assert -1.0 <= m.industry_trend_score < 0 < s.industry_trend_score <= 1.0
    # 同產業成分股共用同一分數
    assert s.industry_trend_score == (await repo.get_latest("S01")).industry_trend_score


async def test_industry_trend_null_when_too_few_members(db_session):
    """成分股不足門檻（每產業各 1 檔）→ 不給分，維持中性 NULL。"""
    await _seed_history(db_session)
    await build_features(db_session, TARGET)
    a = await FeatureDailyRepository(db_session).get_latest("AAA")
    assert a.industry_trend_score is None


async def test_sbl_change_uses_pct_of_own_balance(db_session):
    """借券因子＝相對自身餘額的百分比變化，不是絕對量（OOS 上絕對量版本無訊號）。

    兩檔同樣「多借 100 萬股」，但基期餘額差 10 倍 → 百分比小的那檔 z 必須較低。
    """
    from app.db.models.chips import SblDaily

    await _seed_history(db_session)
    bases = {"AAA": 1_000_000, "CCC": 10_000_000}
    for sym, base in bases.items():
        for i in range(25):
            d = TARGET - dt.timedelta(days=24 - i)
            db_session.add(
                SblDaily(
                    symbol=sym, data_date=d, available_at=availability_for(d),
                    sbl_balance=base + (1_000_000 if i >= 20 else 0),
                )
            )
    # BBB 借券完全不動 → 落在兩者之間的基準
    for i in range(25):
        d = TARGET - dt.timedelta(days=24 - i)
        db_session.add(
            SblDaily(
                symbol="BBB", data_date=d, available_at=availability_for(d),
                sbl_balance=5_000_000,
            )
        )
    await db_session.commit()

    await build_features(db_session, TARGET)
    repo = FeatureDailyRepository(db_session)
    a = await repo.get_latest("AAA")   # +100%
    c = await repo.get_latest("CCC")   # +10%
    b = await repo.get_latest("BBB")   # 0%
    assert a.sbl_change_z > c.sbl_change_z > b.sbl_change_z


async def test_tdcc_change_needs_coverage_threshold(db_session):
    """少數個股有 2 週 TDCC 不足以切 change 模式（否則其餘個股會掉 holder 成分）。"""
    from app.db.models.chips import TdccSummaryWeekly

    await _seed_history(db_session)
    # 只有 AAA 有兩週快照，BBB/CCC 各一週 → 涵蓋率 1/3 < 0.5
    for sym, dates in (
        ("AAA", [TARGET - dt.timedelta(days=7), TARGET]),
        ("BBB", [TARGET]),
        ("CCC", [TARGET]),
    ):
        for i, d in enumerate(dates):
            db_session.add(
                TdccSummaryWeekly(
                    symbol=sym, data_date=d,
                    available_at=availability_for(d),
                    retail_ratio=20 + i, medium_ratio=10,
                    large_ratio=30 + i, super_large_ratio=40,
                    holder_count=10_000 + i * 100,
                )
            )
    await db_session.commit()

    await build_features(db_session, TARGET)
    repo = FeatureDailyRepository(db_session)
    # level proxy 模式：三檔都拿得到 holder 分項（不會有人被排除）
    for sym in ("AAA", "BBB", "CCC"):
        fd = await repo.get_latest(sym)
        assert fd.large_holder_ratio_change_z is not None, sym
