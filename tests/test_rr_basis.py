"""docs/12 §10：API／reason 必須看得出 RR 目標來自前高壓力位還是突破後 ATR 推估。

突破分支的 breakout_target_atr 尚未經 OOS 驗證，使用者需知道該 RR 屬推估值。
"""
from __future__ import annotations

import datetime as dt

import httpx
from httpx import ASGITransport

from app.db.models.features import FeatureDaily
from app.db.models.market import Stock
from app.importers.base import availability_for
from app.models.signal import Action
from app.services.decision import PriceContext, build_risk_plan, decide

CTX = PriceContext(turnover=50_000_000, market_trend_score=0.3, is_locked_limit=False)


def _plan(resistance):
    return build_risk_plan(last_price=100, atr14=2.0, recent_swing_low=96, resistance=resistance)


def test_risk_plan_reports_basis():
    assert _plan(115).rr_basis == "resistance"
    assert _plan(99).rr_basis == "breakout_atr"
    assert _plan(None).rr_basis == "unavailable"


def _decide(resistance, score=82):
    return decide(score=score, reasons=[], last_price=100, atr14=2.0, recent_swing_low=96,
                  price_ctx=CTX, resistance=resistance)


def test_signal_exposes_basis_and_target():
    res = _decide(115)
    assert res.action == Action.BUY
    assert res.rr_basis == "resistance" and res.rr_target == 115


def test_breakout_projection_is_flagged_in_reasons():
    res = _decide(99)
    assert res.rr_basis == "breakout_atr"
    assert any("突破後 ATR 推估" in r and "未經 OOS 驗證" in r for r in res.reasons)


def test_missing_resistance_is_explained():
    res = _decide(None)
    assert res.action == Action.WATCH
    assert any("無壓力位資料" in r for r in res.reasons)


def test_resistance_basis_adds_no_extra_reason():
    assert not any("RR 目標" in r or "推估" in r for r in _decide(115).reasons)


async def test_api_analysis_exposes_rr_basis(db_session):
    from app.db.session import get_session
    from app.main import app

    d = dt.date(2026, 9, 8)
    db_session.add(Stock(symbol="AAA", name="A", market="TWSE"))
    await db_session.flush()
    db_session.add(FeatureDaily(symbol="AAA", data_date=d, available_at=availability_for(d),
                                close=100, atr14=2, recent_swing_low=96, turnover=5e8,
                                resistance_high=120, foreign_5d_z=1.0))
    await db_session.commit()

    async def _override():
        yield db_session

    app.dependency_overrides[get_session] = _override
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            risk = (await c.get("/api/stocks/AAA/analysis")).json()["risk"]
    finally:
        app.dependency_overrides.clear()
    assert risk["rr_basis"] == "resistance" and risk["rr_target"] == 120
