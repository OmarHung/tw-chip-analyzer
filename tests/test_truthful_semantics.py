"""docs/12 Commit B：intraday 與持倉出場語意必須誠實。

- Phase 3A：Distribution Warning 預設關閉；不得用橫斷面 z / level 冒充 slope、rising、raw delta。
- Phase 3B：沒有五檔委買委賣量就沒有 OBI；原本名為 obi 的其實是 CVD slope，正式改名。
- Phase 4：移動停損高於成本時無法推導初始 R，TP 不得退化成成本價。
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from app.core.config import get_thresholds
from app.db.models.features import FeatureDaily
from app.importers.base import availability_for
from app.models.signal import Action, IntradayFeatures, MarketContext
from app.services.analysis import AnalysisService, Position, analyze_market
from app.services.decision import ExitContext, decide
from app.services.decision.exit import decide_exit
from app.services.orderflow_intraday import INTRADAY_SIGNAL_KEYS, compute_orderflow

REPO = Path(__file__).resolve().parents[1]
D = dt.date(2026, 9, 8)


def _warning_ctx(**kw) -> ExitContext:
    base = dict(price=100, stop_loss=90, price_new_high=True,
                cvd_slope=-1.0, large_trade_delta=-500.0, buy_absorption_rising=True)
    base.update(kw)
    return ExitContext(**base)


class TestDistributionWarningDisabled:
    def test_disabled_by_default(self):
        assert get_thresholds().exit_rules["distribution_enabled"] is False
        action, reasons = decide_exit(score=70, ctx=_warning_ctx())
        assert action == Action.HOLD and not any("出貨" in r for r in reasons)

    def test_enabled_requires_all_real_signals(self, monkeypatch):
        monkeypatch.setitem(get_thresholds().exit_rules, "distribution_enabled", True)
        assert decide_exit(score=70, ctx=_warning_ctx())[0] == Action.REDUCE
        for missing in ("cvd_slope", "large_trade_delta", "buy_absorption_rising"):
            action, _ = decide_exit(score=70, ctx=_warning_ctx(**{missing: None}))
            assert action == Action.HOLD, missing

    def test_hard_stop_and_chip_exit_unchanged(self):
        assert decide_exit(70, ExitContext(price=89, stop_loss=90))[0] == Action.EXIT
        assert decide_exit(38, ExitContext(price=100, stop_loss=90))[0] == Action.EXIT
        assert decide_exit(45, ExitContext(price=100, stop_loss=90))[0] == Action.REDUCE

    def test_analysis_does_not_fake_signals_from_zscores(self):
        fd = FeatureDaily(
            symbol="A", data_date=D, available_at=availability_for(D),
            close=130, atr14=2, recent_swing_low=96, resistance_high=130,
            cvd_z=-2.0, large_trade_delta_z=-2.0, absorption_z=2.0,
        )
        ctx = AnalysisService()._exit_ctx(fd, Position(entry_price=100, stop_loss=90), 130, 2, 96, 130)
        assert ctx.cvd_slope is None
        assert ctx.large_trade_delta is None
        assert ctx.buy_absorption_rising is None


class TestCvdSlopeRename:
    def test_config_keys_match_signal_mapping(self):
        keys = set(get_thresholds().weights["intraday"])
        assert keys == set(INTRADAY_SIGNAL_KEYS)
        assert "obi" not in keys and INTRADAY_SIGNAL_KEYS["cvd_slope"] == "cvd_slope_norm"

    def test_no_fake_obi_mapping_in_source(self):
        hits = [
            str(p.relative_to(REPO)) for p in (REPO / "app").rglob("*.py")
            if '"obi": "cvd_slope_norm"' in p.read_text(encoding="utf-8")
        ]
        assert hits == []

    def test_models_use_truthful_names(self):
        assert hasattr(IntradayFeatures(), "cvd_slope_norm")
        assert not hasattr(IntradayFeatures(), "obi")
        assert "cvd_slope_norm" in FeatureDaily.__table__.columns
        assert "intraday_obi" not in FeatureDaily.__table__.columns

    def test_registry_uses_new_key(self):
        from app.core.threshold_registry import REGISTRY

        assert "weights.intraday.cvd_slope" in REGISTRY
        assert "weights.intraday.obi" not in REGISTRY


def _position(entry: float, stop: float | None, price: float, score: float = 80):
    return decide(
        score=score, reasons=[], last_price=price, atr14=2, recent_swing_low=price - 5,
        already_in_position=True,
        exit_ctx=ExitContext(price=price, stop_loss=stop, entry_price=entry),
    )


class TestTrailingStop:
    def test_stop_below_entry_derives_r_multiple_tp(self):
        res = _position(entry=100, stop=95, price=102)  # R=5 → TP1 110 / TP2 115
        assert res.action == Action.HOLD
        assert (res.take_profit_1, res.take_profit_2) == (110, 115)

    def test_stop_above_entry_gives_no_tp(self):
        res = _position(entry=100, stop=105, price=110)
        assert res.action == Action.HOLD
        assert res.take_profit_1 is None and res.take_profit_2 is None
        assert "停損已移至成本以上，無初始風險資料，未自動推導 TP" in res.reasons
        assert not any("已達 TP" in r for r in res.reasons)
        assert res.stop_loss == 105

    def test_stop_equal_entry_gives_no_tp(self):
        res = _position(entry=100, stop=100, price=103)
        assert res.take_profit_1 is None and res.take_profit_2 is None

    def test_stop_at_or_above_price_exits(self):
        assert _position(entry=100, stop=110, price=110).action == Action.EXIT

    def test_reached_tp1_only_hides_tp1(self):
        res = _position(entry=100, stop=95, price=111)
        assert res.take_profit_1 is None and res.take_profit_2 == 115

    def test_analyze_market_accepts_trailing_stop(self):
        items = [
            (FeatureDaily(symbol=f"S{i}", data_date=D, available_at=availability_for(D),
                          close=110, atr14=2, recent_swing_low=100, turnover=5e8,
                          foreign_5d_z=z, trust_5d_z=z), None)
            for i, z in enumerate([-1.0, 0.0, 2.0])
        ]
        res = analyze_market(AnalysisService(), items, MarketContext(0.0),
                             positions={"S2": Position(entry_price=100, stop_loss=105)})
        sig = next(r for r in res if r.symbol == "S2").signal
        assert sig.action in (Action.HOLD, Action.REDUCE)
        assert sig.take_profit_1 is None


def test_unknown_aggressor_stays_unknown():
    ticks = [{"t": i, "price": 100.0, "volume": 10, "side": 0} for i in range(10)]
    r = compute_orderflow(ticks)
    assert r.buy_volume == 0 and r.sell_volume == 0
