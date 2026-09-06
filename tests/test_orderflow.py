"""里程碑 C：Order Flow 演算法單元測試（見 docs/03 §8）。"""
from __future__ import annotations

from app.services.orderflow import absorption, aggressor, cvd, large_trade, obi, trade_speed


class TestAggressor:
    def test_buy_at_ask(self):
        assert aggressor.classify_side(101, bid1=100, ask1=101) == aggressor.BUY

    def test_sell_at_bid(self):
        assert aggressor.classify_side(100, bid1=100, ask1=101) == aggressor.SELL

    def test_tick_rule_fallback(self):
        # 中間價，靠 tick rule
        assert aggressor.classify_side(100.5, 100, 101, prev_price=100.0) == aggressor.BUY
        assert aggressor.classify_side(100.5, 100, 101, prev_price=101.0) == aggressor.SELL

    def test_unknown_not_forced(self):
        assert aggressor.classify_side(100.5, 100, 101, prev_price=100.5) == aggressor.UNKNOWN
        assert aggressor.classify_side(100.5, None, None, prev_price=None) == aggressor.UNKNOWN


class TestCVD:
    def test_delta_and_cumulative(self):
        assert cvd.delta(300, 100) == 200
        assert cvd.cumulative([200, -50, 100]) == [200, 150, 250]

    def test_slope_positive(self):
        assert cvd.slope([0, 1, 2, 3, 4]) > 0

    def test_divergence_distribution(self):
        # 價升但 CVD 降 → 出貨背離 -1
        assert cvd.price_cvd_divergence([100, 105], [500, 300]) == -1
        # 價跌但 CVD 升 → 多方背離 +1
        assert cvd.price_cvd_divergence([105, 100], [300, 500]) == 1


class TestLargeTrade:
    def test_threshold_requires_min_samples(self):
        assert large_trade.large_threshold([10] * 100, min_samples=500) is None
        thr = large_trade.large_threshold(list(range(1000)), percentile=0.95, min_samples=500)
        assert thr is not None and 940 <= thr <= 960

    def test_delta(self):
        trades = [(1, 100), (1, 20), (-1, 200), (-1, 5)]
        # 門檻 50：大買 100，大賣 200 → delta = -100
        assert large_trade.large_trade_delta(trades, threshold=50) == -100

    def test_no_threshold_zero(self):
        assert large_trade.large_trade_delta([(1, 100)], threshold=None) == 0.0


class TestOBI:
    def test_balanced(self):
        assert obi.order_book_imbalance([10, 10], [10, 10]) == 0.0

    def test_bid_heavy(self):
        assert obi.order_book_imbalance([30], [10]) == 0.5

    def test_empty(self):
        assert obi.order_book_imbalance([], []) == 0.0


class TestAbsorption:
    def test_sell_absorption(self):
        # 賣方主導但價沒跌 → sell absorption > 0, buy absorption = 0
        buy_a, sell_a = absorption.directional_absorption(2.0, 0.1, dominant_side=-1)
        assert sell_a > 0 and buy_a == 0

    def test_buy_absorption(self):
        buy_a, sell_a = absorption.directional_absorption(2.0, -0.1, dominant_side=1)
        assert buy_a > 0 and sell_a == 0


class TestTradeSpeed:
    def test_rates(self):
        r = trade_speed.rates(trade_count=60, volume=6000, value=6_000_000, window_sec=60)
        assert r["trades_per_sec"] == 1.0
        assert r["volume_per_sec"] == 100.0
