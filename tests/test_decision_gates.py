"""docs/09 第 1 批：Entry Filter 各 gate 必須用獨立資訊且真正接線。

- BUG-01：RR 以「前高壓力位」為目標、以進場區上緣為成本，不可恆等於 tp1_r。
- BUG-02：Strong Bear 用原始 market_trend_score，不受 Chip Score 市場權重影響。
- BUG-03：鎖死漲停必須擋 BUY；無法判斷時保守視為不可成交。
- BUG-04：持倉上下文可讓公開分析流程產生 HOLD/REDUCE/EXIT。
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from app.db.models.features import FeatureDaily
from app.models.signal import Action, MarketContext
from app.services.analysis import AnalysisService, Position, analyze_market
from app.services.decision import PriceContext, build_risk_plan
from app.services.decision.entry import decide_entry


def _plan(resistance):
    # last=100, atr=2 → entry_high=100.3、stop=97（1.5 ATR；swing 96-0.4 較低故取 97）
    return build_risk_plan(
        last_price=100, atr14=2.0, recent_swing_low=96, resistance=resistance
    )


class TestRiskRewardUsesResistance:
    def test_near_resistance_gives_low_rr(self):
        assert _plan(resistance=103).rr < 2

    def test_far_resistance_gives_high_rr(self):
        assert _plan(resistance=115).rr > 2

    def test_rr_measured_from_entry_high(self):
        p = _plan(resistance=110)
        expected = (110 - p.entry_high) / (p.entry_high - p.stop_loss)
        assert abs(p.rr - round(expected, 2)) < 0.011

    def test_rr_not_tautological_with_tp1_r(self):
        assert _plan(resistance=103).rr != _plan(resistance=115).rr

    def test_breakout_uses_atr_target(self):
        # 壓力位已在進場區上緣之下（突破前高）→ 改用 ATR 倍數目標
        p = _plan(resistance=99)
        assert p.target > p.entry_high
        assert p.rr > 0

    def test_missing_resistance_is_conservative(self):
        assert _plan(resistance=None).rr == 0.0

    def test_rr_below_equal_above_threshold(self):
        ctx = PriceContext(turnover=50_000_000, market_trend_score=0.3, is_locked_limit=False)
        assert decide_entry(80, 1.99, ctx).action == Action.WATCH
        assert decide_entry(80, 2.0, ctx).action == Action.BUY
        assert decide_entry(80, 2.5, ctx).action == Action.BUY


class TestLockedLimit:
    def _ctx(self, locked):
        return PriceContext(
            turnover=50_000_000, market_trend_score=0.3, is_locked_limit=locked
        )

    def test_locked_limit_blocks_buy(self):
        d = decide_entry(80, 2.5, self._ctx(True))
        assert d.action == Action.WATCH
        assert any("無法合理成交" in g for g in d.gate_reasons)

    def test_unknown_lock_state_is_conservative(self):
        assert decide_entry(80, 2.5, self._ctx(None)).action == Action.WATCH

    @staticmethod
    def _bars(n: int, last_close: float, last_high: float, last_low: float) -> pd.DataFrame:
        base = dt.date(2026, 6, 1)
        return pd.DataFrame(
            {
                "data_date": [base + dt.timedelta(days=i) for i in range(n)],
                "high": [101.0] * (n - 1) + [last_high],
                "low": [99.0] * (n - 1) + [last_low],
                "close": [100.0] * (n - 1) + [last_close],
                "volume": [1_000_000] * n,
                "turnover": [1e8] * n,
            }
        )

    def test_price_features_detects_locked_limit(self):
        from app.services.feature_builder import _price_features

        assert _price_features(self._bars(25, 110, 110, 110))["is_limit_locked"] is True
        # 漲停但盤中曾打開（high != low）→ 非鎖死
        assert _price_features(self._bars(25, 110, 110, 105))["is_limit_locked"] is False
        # 平盤一字（量極小的冷門股）不是漲停
        assert _price_features(self._bars(25, 100, 100, 100))["is_limit_locked"] is False

    def test_price_features_resistance_high(self):
        from app.services.feature_builder import _price_features

        g = self._bars(30, 98, 100, 95)
        g.loc[5, "high"] = 130.0
        assert _price_features(g)["resistance_high"] == 130.0
        # 不足最低 bar 數 → NULL
        assert _price_features(g.tail(5))["resistance_high"] is None


def _fd(symbol: str, foreign: float, **kw) -> FeatureDaily:
    base = dict(
        symbol=symbol, data_date=dt.date(2026, 9, 4),
        available_at=dt.datetime(2026, 9, 4, 15, 0),
        close=100, atr14=2, ma20=100, vwap=100, recent_swing_low=96,
        turnover=5e8, close_vs_ma20_pct=0.0, close_vs_vwap_pct=0.0,
        foreign_5d_z=foreign, trust_5d_z=foreign,
        resistance_high=130, is_limit_locked=False,
    )
    base.update(kw)
    return FeatureDaily(**base)


def _items(zs):
    return [(_fd(f"S{i}", z), None) for i, z in enumerate(zs)]


FIVE = [-2.0, -1.0, 0.0, 1.0, 3.0]  # 百分位 10/30/50/70/90


class TestStrongBearUsesRawTrend:
    def test_top_stock_is_buy_in_neutral_market(self):
        res = analyze_market(AnalysisService(), _items(FIVE), MarketContext(0.0))
        assert res[-1].signal.action == Action.BUY

    def test_raw_trend_at_threshold_blocks_buy(self):
        # 原始 -0.5：舊實作只看到 0.65 × -0.5 = -0.325 而放行
        res = analyze_market(AnalysisService(), _items(FIVE), MarketContext(-0.5))
        top = res[-1].signal
        assert top.action == Action.WATCH
        assert any("Strong Bear" in r for r in top.reasons)


class TestLockedLimitWiredIntoAnalysis:
    def test_locked_top_stock_is_watch(self):
        items = _items(FIVE)
        items[-1][0].is_limit_locked = True
        res = analyze_market(AnalysisService(), items)
        assert res[-1].signal.action == Action.WATCH


class TestPositionContext:
    def test_same_data_differs_by_position(self):
        svc = AnalysisService()
        # 明確帶「已知中性大盤」：未知大盤不放行 BUY（docs/12 Phase 1）
        known = MarketContext(0.0)
        flat = analyze_market(svc, _items(FIVE), known)
        held = analyze_market(
            svc, _items(FIVE), known, positions={"S4": Position(entry_price=95, stop_loss=90)}
        )
        assert flat[-1].signal.action == Action.BUY
        assert held[-1].signal.action == Action.HOLD
        assert held[-1].signal.stop_loss == 90
        assert held[-1].signal.entry_zone is None

    def test_hard_stop_exit(self):
        res = analyze_market(
            AnalysisService(), _items(FIVE),
            positions={"S4": Position(entry_price=110, stop_loss=101)},
        )
        assert res[-1].signal.action == Action.EXIT

    def test_weak_scores_reduce_and_exit(self):
        # 10 檔 → 百分位 5,15,...,95；S4=45 落在 [exit 40, reduce 50) → REDUCE
        pos = Position(entry_price=100, stop_loss=80)
        zs = [float(i) for i in range(10)]
        res = {
            r.symbol: r.signal.action
            for r in analyze_market(
                AnalysisService(), _items(zs), positions={"S0": pos, "S4": pos}
            )
        }
        assert res["S0"] == Action.EXIT
        assert res["S4"] == Action.REDUCE

    def test_default_stop_derived_from_entry_price(self):
        res = analyze_market(
            AnalysisService(), _items(FIVE), positions={"S4": Position(entry_price=100)}
        )
        sig = res[-1].signal
        assert sig.action == Action.HOLD
        assert sig.stop_loss is not None and sig.stop_loss < 100


class TestPositionTakeProfitReached:
    def test_reached_tp_hidden_with_reason(self):
        from app.services.decision import ExitContext, decide

        # 成本 2000、停損 1900 → TP1 2200、TP2 2300；現價 2250 已過 TP1、未達 TP2
        res = decide(
            score=80, reasons=[], last_price=2250, atr14=30, recent_swing_low=2100,
            already_in_position=True,
            exit_ctx=ExitContext(price=2250, stop_loss=1900, entry_price=2000),
        )
        assert res.action == Action.HOLD
        assert res.take_profit_1 is None
        assert res.take_profit_2 == 2300
        assert any("已達 TP1" in r and "2200" in r for r in res.reasons)
        assert not any("TP2" in r for r in res.reasons)
