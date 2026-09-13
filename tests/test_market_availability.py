"""docs/12 Commit A：同日大盤上下文（Phase 1）+ institutional 子項可用性分組（Phase 2）。

Phase 1：MarketDaily 只接受目標日；未知時 market 成分排除、Entry Filter 輸出 WATCH，
不得沿用前一日 regime 或以 0 冒充中性。
Phase 2：institutional 子項覆蓋率不同（例如 TPEx 融資來源失敗）不得混在同一百分位組；
零權重的 SBL 有無不切組；單檔小組退回 linear，不給假的 0/100。
"""
from __future__ import annotations

import datetime as dt

import httpx
import pytest
from httpx import ASGITransport

from app.db.models.features import FeatureDaily, SignalSnapshot
from app.db.models.market import MarketDaily, Stock
from app.importers.base import availability_for
from app.models.signal import Action, MarketContext
from app.repositories.market import load_market_context, load_market_daily
from app.services.analysis import AnalysisService, analyze_market
from app.services.signal_persist import persist_signals

TODAY = dt.date(2026, 9, 8)
PREV = dt.date(2026, 9, 7)


def _md(d: dt.date, score: float) -> MarketDaily:
    return MarketDaily(data_date=d, available_at=availability_for(d), taiex_close=20000,
                       market_trend_score=score)


def _fd(sym: str, z: float, **kw) -> FeatureDaily:
    base = dict(
        symbol=sym, data_date=TODAY, available_at=availability_for(TODAY),
        close=100, atr14=2, ma20=100, vwap=100, recent_swing_low=96, turnover=5e8,
        close_vs_ma20_pct=0.0, close_vs_vwap_pct=0.0, resistance_high=130,
        is_limit_locked=False, foreign_5d_z=z, trust_5d_z=z, dealer_5d_z=z,
        margin_balance_change_z=-z, short_balance_change_z=-z,
    )
    base.update(kw)
    return FeatureDaily(**base)


# ---------------- Phase 1 ----------------

async def test_context_does_not_fall_back_to_previous_day(db_session):
    db_session.add(_md(PREV, 0.6))
    await db_session.commit()
    ctx = await load_market_context(db_session, TODAY)
    assert ctx.market_trend_score is None
    assert await load_market_daily(db_session, TODAY) is None
    assert (await load_market_context(db_session, PREV)).market_trend_score == 0.6


def test_unknown_market_excludes_market_component():
    r = AnalysisService().score_features(_fd("A", 1.0), MarketContext(market_trend_score=None))
    assert "market" not in r.components
    known = AnalysisService().score_features(_fd("A", 1.0), MarketContext(market_trend_score=0.2))
    assert "market" in known.components


def test_default_market_context_is_unknown():
    assert MarketContext().market_trend_score is None


def _five():
    return [(_fd(f"S{i}", z), None) for i, z in enumerate([-2.0, -1.0, 0.0, 1.0, 3.0])]


def test_unknown_market_blocks_buy_even_if_everything_else_passes():
    top = analyze_market(AnalysisService(), _five(), MarketContext())[-1].signal
    assert top.action == Action.WATCH
    assert "無當日大盤資料，無法排除 Strong Bear" in top.reasons


def test_known_market_behaviour_unchanged():
    assert analyze_market(AnalysisService(), _five(), MarketContext(0.3))[-1].signal.action == Action.BUY
    bear = analyze_market(AnalysisService(), _five(), MarketContext(-0.6))[-1].signal
    assert bear.action == Action.WATCH and any("Strong Bear" in r for r in bear.reasons)


async def test_snapshot_records_market_unavailable(db_session):
    db_session.add(Stock(symbol="AAA", name="A", market="TWSE"))
    db_session.add(_md(PREV, 0.6))  # 只有前一日
    await db_session.flush()
    db_session.add(_fd("AAA", 1.0))
    await db_session.commit()
    await persist_signals(db_session, TODAY)
    snap = (await db_session.execute(SignalSnapshot.__table__.select())).one()
    assert snap.payload["market_available"] is False
    assert snap.payload["market_trend_score"] is None


async def test_dashboard_does_not_label_yesterday_market_as_today(db_session):
    from app.db.session import get_session
    from app.main import app

    db_session.add(Stock(symbol="AAA", name="A", market="TWSE"))
    db_session.add(_md(PREV, 0.6))
    await db_session.flush()
    db_session.add(_fd("AAA", 1.0))
    await db_session.commit()

    async def _override():
        yield db_session

    app.dependency_overrides[get_session] = _override
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            body = (await c.get("/api/dashboard")).json()
    finally:
        app.dependency_overrides.clear()
    assert body["as_of"] == str(TODAY)
    assert body["market"] is None


# ---------------- Phase 2 ----------------

def _pct(results):
    return {r.symbol: r.chip.chip_score for r in results}


def test_missing_margin_short_is_separate_percentile_group():
    zs = [-2.0, -1.0, 0.0, 1.0, 2.0]
    full = [(_fd(f"F{i}", z), None) for i, z in enumerate(zs)]
    partial = [
        (_fd(f"P{i}", z, margin_balance_change_z=None, short_balance_change_z=None), None)
        for i, z in enumerate(zs)
    ]
    res = analyze_market(AnalysisService(), full + partial, MarketContext(0.0))
    scores = _pct(res)
    for prefix in ("F", "P"):
        assert sorted(scores[f"{prefix}{i}"] for i in range(5)) == [10.0, 30.0, 50.0, 70.0, 90.0]
    sig = {r.symbol: r.chip.availability_signature for r in res}
    assert sig["F0"] != sig["P0"]
    assert sig["F0"] >= res[0].chip.components  # signature 涵蓋頂層成分


def test_zero_weight_sbl_does_not_split_groups():
    zs = [-2.0, -1.0, 0.0, 1.0, 2.0]
    items = [(_fd(f"A{i}", z, sbl_change_z=0.5), None) for i, z in enumerate(zs)] + [
        (_fd(f"B{i}", z + 0.1, sbl_change_z=None), None) for i, z in enumerate(zs)
    ]
    res = analyze_market(AnalysisService(), items, MarketContext(0.0))
    assert len({r.chip.availability_signature for r in res}) == 1
    assert sorted(_pct(res).values()) == [5.0, 15.0, 25.0, 35.0, 45.0, 55.0, 65.0, 75.0, 85.0, 95.0]


def test_singleton_group_falls_back_to_linear_not_extreme():
    zs = [-2.0, -1.0, 0.0, 1.0, 2.0]
    items = [(_fd(f"F{i}", z), None) for i, z in enumerate(zs)] + [
        (_fd("LONE", 3.0, margin_balance_change_z=None, short_balance_change_z=None), None)
    ]
    lone = next(r for r in analyze_market(AnalysisService(), items, MarketContext(0.0))
                if r.symbol == "LONE")
    assert lone.chip.chip_score not in (0.0, 100.0)
    expected = 50 + 50 * min(max(lone.chip.composite_raw, -1), 1)
    assert lone.chip.chip_score == pytest.approx(expected, abs=0.11)


def test_all_effective_institutional_missing_still_excludes_component():
    fd = _fd("X", 0.0, foreign_5d_z=None, trust_5d_z=None, dealer_5d_z=None,
             margin_balance_change_z=None, short_balance_change_z=None, sbl_change_z=1.0)
    r = AnalysisService().score_features(fd, MarketContext(0.0))
    assert "institutional" not in r.components
    assert not any(s.startswith("institutional:") for s in r.availability_signature)
