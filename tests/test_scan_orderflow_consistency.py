"""docs/09 第 3 批：背離掃描快取/新鮮度/還原（BUG-06/14）、盤中分數權重來源（BUG-13）。"""
from __future__ import annotations

import datetime as dt

from app.db.models.chips import InstitutionalDaily
from app.db.models.market import CorporateAction, DailyPrice, Stock
from app.importers.base import availability_for
from app.services import flow_scan
from app.services.orderflow_intraday import INTRADAY_SIGNAL_KEYS, compute_orderflow

START = dt.date(2026, 3, 2)


def _d(i: int) -> dt.date:
    return START + dt.timedelta(days=i)


async def _seed(session, days: int = 80):
    """UP：穩定上漲。HALT：最後 10 天停牌。SPL：day60 1 拆 2（價減半、量加倍）。"""
    for sym in ("UP", "HALT", "SPL"):
        session.add(Stock(symbol=sym, name=sym, market="TWSE"))
    await session.flush()
    for i in range(days):
        d, av = _d(i), availability_for(_d(i))
        specs = {"UP": 100 * (1.004 ** i), "SPL": (100.0 if i < 60 else 50.0)}
        if i < days - 10:
            specs["HALT"] = 100 * (1.004 ** i)
        for sym, px in specs.items():
            vol = 2_000_000 if (sym == "SPL" and i >= 60) else 1_000_000
            session.add(DailyPrice(symbol=sym, data_date=d, available_at=av, open=px, high=px,
                                   low=px, close=px, volume=vol, turnover=px * vol))
            session.add(InstitutionalDaily(symbol=sym, data_date=d, available_at=av,
                                           foreign_net=-50_000, trust_net=0,
                                           dealer_self_net=0, dealer_hedge_net=0))
    session.add(CorporateAction(symbol="SPL", data_date=_d(60), available_at=availability_for(_d(59)),
                                kind="面額", prev_close=100, reference_price=50,
                                adj_factor=0.5, share_factor=2.0))
    await session.commit()


async def test_cache_respects_window(db_session):
    flow_scan._cache.clear()
    await _seed(db_session)
    _, r60 = await flow_scan.scan_divergence(db_session, window=60)
    _, r20 = await flow_scan.scan_divergence(db_session, window=20)
    up60 = next(r for r in r60 if r.symbol == "UP")
    up20 = next(r for r in r20 if r.symbol == "UP")
    assert up60.window == 60 and up20.window == 20
    assert up60.price_return > up20.price_return  # 60 日累積漲幅大於 20 日


async def test_halted_stock_excluded(db_session):
    flow_scan._cache.clear()
    await _seed(db_session)
    _, rows = await flow_scan.scan_divergence(db_session, window=20)
    assert "HALT" not in {r.symbol for r in rows}


async def test_split_does_not_create_fake_price_drop(db_session):
    flow_scan._cache.clear()
    await _seed(db_session)
    _, rows = await flow_scan.scan_divergence(db_session, window=60)
    spl = next(r for r in rows if r.symbol == "SPL")
    assert abs(spl.price_return) < 0.01  # 未還原時是 -50%
    assert spl.change_pct is not None and abs(spl.change_pct) < 1e-9


def _tick(t, vol, side):
    return {"t": t, "price": 100.0, "volume": vol, "side": side}


class TestOrderflowScoreWeights:
    def test_signal_keys_match_config_weights(self):
        from app.core.config import get_thresholds

        assert set(INTRADAY_SIGNAL_KEYS) == set(get_thresholds().weights["intraday"])

    def test_score_follows_config_weights(self, monkeypatch):
        from app.core.config import get_thresholds

        # 全外盤買 → net_aggressor = 1（對應 config 的 cvd 項）
        ticks = [_tick(60 * i, 10, 1) for i in range(600)]
        w = get_thresholds().weights["intraday"]
        only_cvd = {k: (1.0 if k == "cvd" else 0.0) for k in w}
        monkeypatch.setitem(get_thresholds().weights, "intraday", only_cvd)
        r = compute_orderflow(ticks)
        assert r.net_aggressor == 1.0
        assert r.intraday_score == 100.0
