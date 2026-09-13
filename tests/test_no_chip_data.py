"""無任何籌碼成分（法人/集保/盤中皆缺）的標的不評分。

實例：上櫃行情 2026-05-11 才開始累積，前 20 個交易日 avg_vol20 無定義 → 法人 z 全 NULL，
又無 TDCC、無逐筆 → 只剩全市場共用的大盤成分。該組 composite_raw 全部相同，百分位全為 50，
每日約 800 檔以「50 分 / HOLD」落地，灌進驗證頁 [50,60) bucket 與 IC。
缺資料要排除、不可以中性值冒充（docs/03 §10、docs/13 §3）。
"""
from __future__ import annotations

import datetime as dt

import httpx
from httpx import ASGITransport
from sqlalchemy import select

from app.db.models.chips import InstitutionalDaily
from app.db.models.features import FeatureDaily, SignalSnapshot
from app.db.models.market import DailyPrice, Stock
from app.db.session import get_session
from app.importers.base import availability_for
from app.main import app
from app.models.signal import DailyFeatures, IntradayFeatures, MarketContext, WeeklyFeatures
from app.services.chip.composite import ChipScorer
from app.services.feature_builder import build_features
from app.services.market_scan import scan_all
from app.services.signal_persist import persist_signals
from tests.test_signal_persist import TARGET, _seed_history


_NO_INST = dict(foreign_5d_z=None, trust_5d_z=None, dealer_5d_z=None,
                margin_balance_change_z=None, short_balance_change_z=None, sbl_change_z=None)


def test_market_only_result_has_no_chip_data():
    scorer = ChipScorer()
    market_only = scorer.score(
        IntradayFeatures(), DailyFeatures(**_NO_INST), WeeklyFeatures(), MarketContext(0.4),
        active_components={"institutional", "market"},
    )
    assert market_only.components == frozenset({"market"})
    assert market_only.has_chip_data is False

    with_inst = scorer.score(
        IntradayFeatures(), DailyFeatures(**{**_NO_INST, "foreign_5d_z": 1.0}), WeeklyFeatures(), MarketContext(0.4),
        active_components={"institutional", "market"},
    )
    assert with_inst.has_chip_data is True


async def _seed_with_new_listing(session):
    await _seed_history(session)
    # NEW：只有最近 3 天行情與法人 → 20 日均量無定義 → 法人 z 全 NULL；無 TDCC、無逐筆
    session.add(Stock(symbol="NEW", name="新股", market="TPEx"))
    await session.flush()
    for k in range(3):
        d = TARGET - dt.timedelta(days=2 - k)
        av = availability_for(d)
        session.add(DailyPrice(symbol="NEW", data_date=d, available_at=av, open=30, high=31,
                               low=29, close=30, volume=500_000, turnover=15_000_000))
        session.add(InstitutionalDaily(symbol="NEW", data_date=d, available_at=av, foreign_net=1000,
                                       trust_net=0, dealer_self_net=0, dealer_hedge_net=0))
    await session.commit()
    await build_features(session, TARGET)
    fd = (await session.execute(
        select(FeatureDaily).where(FeatureDaily.symbol == "NEW", FeatureDaily.data_date == TARGET)
    )).scalar_one()
    # 前提：NEW 確實沒有任何籌碼特徵
    assert fd.foreign_5d_z is None and fd.large_holder_ratio_change_z is None and fd.cvd_z is None


async def test_persist_skips_and_removes_stale_unscorable_snapshot(db_session):
    await _seed_with_new_listing(db_session)
    # 修正前已落地的假 50 分列：重建時必須清掉，upsert 不會自己刪
    db_session.add(SignalSnapshot(symbol="NEW", data_date=TARGET,
                                  available_at=availability_for(TARGET),
                                  chip_score=50.0, action="HOLD"))
    await db_session.commit()

    await persist_signals(db_session, TARGET)

    syms = set((await db_session.execute(
        select(SignalSnapshot.symbol).where(SignalSnapshot.data_date == TARGET)
    )).scalars())
    assert syms == {"AAA", "BBB", "CCC"}


async def test_scan_and_analysis_api_exclude_unscorable(db_session):
    await _seed_with_new_listing(db_session)

    _, rows = await scan_all(db_session)
    assert "NEW" not in {r.symbol for r in rows}
    assert {"AAA", "BBB", "CCC"} <= {r.symbol for r in rows}

    async def _override():
        yield db_session

    app.dependency_overrides[get_session] = _override
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            resp = await c.get("/api/stocks/NEW/analysis")
            ok = await c.get("/api/stocks/AAA/analysis")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 404
    assert "籌碼資料不足" in resp.json()["detail"]
    assert ok.status_code == 200
