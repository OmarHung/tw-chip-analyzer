"""Backtest 引擎測試（見 docs/06 §17-18）。"""
from __future__ import annotations

import datetime as dt

from app.backtest import Bar, BacktestEngine, BacktestSignal, CostModel
from app.backtest.forward_returns import compute_forward
from app.backtest.metrics import summarize


def _bar(day: int, o, h, l, c) -> Bar:
    return Bar(date=dt.date(2026, 1, day), open=o, high=h, low=l, close=c)


class TestCostModel:
    def test_costs_nonzero(self):
        c = CostModel()
        assert c.round_trip_cost > 0

    def test_net_below_gross(self):
        c = CostModel()
        entry, exit_ = 100.0, 110.0
        gross = (exit_ - entry) / entry
        assert c.net_return(entry, exit_) < gross

    def test_flat_price_is_negative_after_costs(self):
        # 進出同價 → 因成本必為負報酬（禁止 0 成本推論有效）
        c = CostModel()
        assert c.net_return(100, 100) < 0


class TestForwardReturns:
    def test_horizons_and_mfe_mae(self):
        entry = 100.0
        bars = [
            _bar(1, 100, 105, 99, 102),   # k=1
            _bar(2, 102, 108, 101, 107),  # k=2
            _bar(3, 107, 112, 95, 96),    # k=3 收黑，低點 95
        ]
        c = CostModel(fee_rate=0, tax_rate=0, slippage_pct=0)  # 純 gross 驗證
        r = compute_forward(entry, bars, [1, 2, 3], c, bars[0].date)
        # 1D：close 102 → +2%
        assert abs(r.horizons[1].gross_return - 0.02) < 1e-9
        # 3D：close 96 → -4%
        assert abs(r.horizons[3].gross_return + 0.04) < 1e-9
        # MFE 到第3天最高 112 → +12%
        assert abs(r.horizons[3].mfe - 0.12) < 1e-9
        # MAE 到第3天最低 95 → -5%
        assert abs(r.horizons[3].mae + 0.05) < 1e-9

    def test_insufficient_data_dropped(self):
        r = compute_forward(100, [_bar(1, 100, 101, 99, 100)], [1, 5], CostModel(), dt.date(2026, 1, 1))
        assert 1 in r.horizons and 5 in r.dropped_horizons


class TestLookahead:
    def test_entry_strictly_after_data_date(self):
        eng = BacktestEngine()
        bars = [
            _bar(5, 50, 51, 49, 50),   # 訊號當日(data_date=1/5)，不可當進場
            _bar(6, 52, 55, 51, 54),   # 進場 bar（open=52）
            _bar(7, 54, 58, 53, 57),
        ]
        sig = BacktestSignal("2330", dt.date(2026, 1, 5), 80)
        oc = eng.evaluate_signal(sig, bars)
        assert oc is not None
        assert oc.forward.entry_date == dt.date(2026, 1, 6)
        assert oc.forward.entry_price == 52  # 用進場 bar 的 open，非訊號日收盤

    def test_no_future_bar_returns_none(self):
        eng = BacktestEngine()
        bars = [_bar(5, 50, 51, 49, 50)]
        assert eng.evaluate_signal(BacktestSignal("X", dt.date(2026, 1, 5), 80), bars) is None


class TestMetrics:
    def test_summarize(self):
        s = summarize([0.02, -0.01, 0.03, -0.02, 0.05])
        assert s.count == 5
        assert 0 < s.win_rate < 1
        assert s.profit_factor > 0

    def test_max_drawdown_nonpositive(self):
        s = summarize([0.05, -0.03, -0.04, 0.02])
        assert s.max_drawdown <= 0


class TestEngineAggregation:
    def _linear_series(self, start_day: int, entry_price: float, daily_ret: float, n=25):
        """從 start_day 起，每日以固定報酬率上漲/下跌的合成序列。"""
        bars = []
        price = entry_price
        for i in range(n):
            o = price
            c = price * (1 + daily_ret)
            bars.append(_bar(start_day + i, o, max(o, c), min(o, c), c))
            price = c
        return bars

    def test_higher_score_higher_return_monotonic(self):
        """核心成功標準：score 越高 → 5D 報酬越好。"""
        eng = BacktestEngine()
        prices = {}
        signals = []
        # 三組股票，score 越高日報酬越高
        specs = [("LOW", 55, 0.000), ("MID", 72, 0.004), ("HIGH", 85, 0.010)]
        for sym, score, dr in specs:
            prices[sym] = self._linear_series(1, 100, dr)
            # data_date=1/1，進場為 1/2
            signals.append(BacktestSignal(sym, dt.date(2026, 1, 1), score))

        report = eng.run(signals, prices)
        assert report.evaluated == 3

        def avg5(label):
            br = next(b for b in report.by_bucket if b.label == label)
            return br.by_horizon[5].avg_return

        # bucket [50,60) < [70,80) < [80,90)
        assert avg5("[50,60)") < avg5("[70,80)") < avg5("[80,90)")

    def test_dropped_when_no_prices(self):
        eng = BacktestEngine()
        report = eng.run([BacktestSignal("NOPE", dt.date(2026, 1, 1), 80)], {})
        assert report.evaluated == 0 and report.dropped == 1
