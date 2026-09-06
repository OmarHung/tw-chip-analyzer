"""盤中 order flow 計算測試（見 docs/03 §8）。"""
from __future__ import annotations

from app.services.orderflow_intraday import compute_orderflow


def _tick(t, price, vol, side):
    return {"t": t, "price": price, "volume": vol, "side": side}


class TestOrderFlow:
    def test_empty(self):
        r = compute_orderflow([])
        assert r.trade_count == 0 and r.intraday_score == 50.0

    def test_buy_dominant_bullish(self):
        # 買方(外盤)主導 → net_aggressor>0、分數>50
        ticks = [_tick(60 * i, 100, 10, 1) for i in range(600)]
        ticks += [_tick(60 * 600 + i, 100, 1, -1) for i in range(50)]
        r = compute_orderflow(ticks)
        assert r.buy_volume > r.sell_volume
        assert r.net_aggressor > 0
        assert r.intraday_score > 50

    def test_sell_dominant_bearish(self):
        ticks = [_tick(60 * i, 100, 10, -1) for i in range(600)]
        r = compute_orderflow(ticks)
        assert r.net_aggressor < 0
        assert r.intraday_score < 50

    def test_cvd_and_series(self):
        ticks = [_tick(0, 100, 5, 1), _tick(1, 100, 3, -1), _tick(61, 100, 4, 1)]
        r = compute_orderflow(ticks)
        # CVD = 5 -3 +4 = 6
        assert r.cvd_final == 6
        # 兩個分鐘桶
        assert len(r.cvd_series) == 2

    def test_large_trade_detection(self):
        # 大量買單 + 眾多小賣單 → large_net 偏多
        ticks = [_tick(i, 100, 1, -1) for i in range(1000)]
        ticks.append(_tick(1001, 100, 5000, 1))  # 明顯大單買
        r = compute_orderflow(ticks)
        assert r.large_threshold is not None
        assert r.large_buy >= 5000
        assert r.large_net > 0

    def test_buy_ratio(self):
        ticks = [_tick(0, 100, 7, 1), _tick(1, 100, 3, -1)]
        r = compute_orderflow(ticks)
        assert abs(r.buy_ratio - 0.7) < 1e-6
