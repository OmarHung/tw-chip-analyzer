"""里程碑 D：決策引擎測試（見 docs/04）。"""
from __future__ import annotations

from app.models.signal import Action
from app.services.decision import ExitContext, PriceContext, build_risk_plan, decide
from app.services.decision.entry import decide_entry
from app.services.decision.exit import decide_exit


class TestRiskPlan:
    def test_tp_and_rr(self):
        p = build_risk_plan(last_price=100, atr14=2.0, recent_swing_low=96)
        assert p.stop_loss < 100
        assert p.tp1 > 100 and p.tp2 > p.tp1
        r = 100 - p.stop_loss
        assert abs(p.tp1 - (100 + 2 * r)) < 0.05  # tp1_r=2
        assert p.rr == 2.0

    def test_max_stop_pct_floor(self):
        # ATR 很大時，停損被 max_stop_pct(6%) 地板限制住
        p = build_risk_plan(last_price=100, atr14=50, recent_swing_low=10)
        assert p.stop_loss >= 100 * (1 - 0.06) - 0.01


class TestEntryFilter:
    def _good_ctx(self):
        return PriceContext(
            close_vs_vwap_pct=0.01, close_vs_ma20_pct=0.02,
            turnover=50_000_000, market_score_norm=0.3,
        )

    def test_buy_when_all_pass(self):
        d = decide_entry(score=80, rr=2.5, ctx=self._good_ctx())
        assert d.action == Action.BUY and not d.gate_reasons

    def test_high_score_but_overextended_becomes_watch(self):
        ctx = self._good_ctx()
        ctx.close_vs_ma20_pct = 0.20  # 過度延伸
        d = decide_entry(score=80, rr=2.5, ctx=ctx)
        assert d.action == Action.WATCH
        assert any("MA20" in g for g in d.gate_reasons)

    def test_high_score_low_rr_becomes_watch(self):
        d = decide_entry(score=80, rr=1.2, ctx=self._good_ctx())
        assert d.action == Action.WATCH
        assert any("風險報酬" in g for g in d.gate_reasons)

    def test_strong_bear_blocks_buy(self):
        ctx = self._good_ctx()
        ctx.market_score_norm = -0.7
        d = decide_entry(score=80, rr=2.5, ctx=ctx)
        assert d.action == Action.WATCH

    def test_watch_band(self):
        assert decide_entry(score=68, rr=2.5, ctx=self._good_ctx()).action == Action.WATCH

    def test_avoid_low_score(self):
        assert decide_entry(score=30, rr=2.5, ctx=self._good_ctx()).action == Action.AVOID


class TestExitLogic:
    def test_hard_stop(self):
        action, _ = decide_exit(score=70, ctx=ExitContext(price=90, stop_loss=95))
        assert action == Action.EXIT

    def test_chip_exit(self):
        action, _ = decide_exit(score=38, ctx=ExitContext(price=100, stop_loss=90))
        assert action == Action.EXIT

    def test_chip_reduce(self):
        action, _ = decide_exit(score=45, ctx=ExitContext(price=100, stop_loss=90))
        assert action == Action.REDUCE

    def test_distribution_warning(self):
        ctx = ExitContext(
            price=100, stop_loss=90, price_new_high=True,
            cvd_slope=-1.0, large_trade_delta=-500, buy_absorption_rising=True,
        )
        action, reasons = decide_exit(score=70, ctx=ctx)
        assert action == Action.REDUCE and any("出貨" in r for r in reasons)

    def test_hold(self):
        action, _ = decide_exit(score=70, ctx=ExitContext(price=100, stop_loss=90))
        assert action == Action.HOLD


class TestDecideOrchestration:
    def test_buy_end_to_end(self):
        res = decide(
            score=82, reasons=["投信買超"], last_price=100, atr14=2.0, recent_swing_low=96,
            price_ctx=PriceContext(turnover=50_000_000, market_score_norm=0.3),
        )
        assert res.action == Action.BUY
        assert res.entry_zone is not None and res.risk_reward >= 2

    def test_in_position_exit_clears_entry_zone(self):
        res = decide(
            score=35, reasons=[], last_price=100, atr14=2.0, recent_swing_low=96,
            already_in_position=True,
        )
        assert res.action == Action.EXIT
        assert res.entry_zone is None
