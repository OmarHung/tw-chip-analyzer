"""主力進出量價背離偵測單元測試（app.services.flows.compute_divergence）。"""
from __future__ import annotations

from app.services.flows import compute_cost_basis, compute_divergence

# 20 個交易日,成交量固定 1000 張/日;門檻沿用 config 預設語意。
_KW = dict(window=20, price_eps=0.03, flow_eps=0.02, min_points=10)
_VOL = [1000.0] * 20


def _closes(start: float, end: float) -> list[float]:
    step = (end - start) / 19
    return [round(start + step * i, 4) for i in range(20)]


class TestDivergence:
    def test_bullish_divergence(self):
        # 價跌 10%、主力持續買超 → 正背離
        r = compute_divergence(_closes(100, 90), [100.0] * 20, _VOL, **_KW)
        assert r is not None
        assert r.status == "bullish_div"
        assert r.price_return is not None and r.price_return < 0
        assert r.inst_flow_ratio is not None and r.inst_flow_ratio > 0

    def test_bearish_divergence(self):
        # 價漲 ~11%、主力持續賣超 → 負背離
        r = compute_divergence(_closes(90, 100), [-100.0] * 20, _VOL, **_KW)
        assert r is not None
        assert r.status == "bearish_div"

    def test_aligned_up(self):
        # 價漲、主力買超 → 量價同向偏多
        r = compute_divergence(_closes(90, 100), [100.0] * 20, _VOL, **_KW)
        assert r is not None
        assert r.status == "aligned_up"

    def test_aligned_down(self):
        r = compute_divergence(_closes(100, 90), [-100.0] * 20, _VOL, **_KW)
        assert r is not None
        assert r.status == "aligned_down"

    def test_neutral_when_moves_small(self):
        # 價與主力幅度皆低於門檻 → 中性
        r = compute_divergence(_closes(100, 100.5), [1.0] * 20, _VOL, **_KW)
        assert r is not None
        assert r.status == "neutral"

    def test_neutral_when_insufficient_points(self):
        # 有效樣本不足 min_points → 中性
        closes = [None] * 15 + _closes(100, 90)[:5]
        insts = [None] * 15 + [100.0] * 5
        r = compute_divergence(closes, insts, _VOL, **_KW)
        assert r is not None
        assert r.status == "neutral"

    def test_flow_ratio_normalized_by_turnover(self):
        # 同樣淨買超,成交量翻倍 → 佔比減半(正規化正確,鐵則 6)
        r1 = compute_divergence(_closes(100, 90), [100.0] * 20, [1000.0] * 20, **_KW)
        r2 = compute_divergence(_closes(100, 90), [100.0] * 20, [2000.0] * 20, **_KW)
        assert r1 is not None and r2 is not None
        assert r1.inst_flow_ratio is not None and r2.inst_flow_ratio is not None
        assert abs(r1.inst_flow_ratio - 2 * r2.inst_flow_ratio) < 1e-9

    def test_length_mismatch_raises(self):
        import pytest

        with pytest.raises(ValueError):
            compute_divergence([100.0], [1.0, 2.0], [1000.0], **_KW)


class TestCostBasis:
    def test_weighted_average_accumulation(self):
        # 買 100張@10、再買 100張@20 → 均價 15;現價 20 → 浮盈
        r = compute_cost_basis([10.0, 20.0], [100.0, 100.0], state_eps=0.01)
        assert r.latest_cost == 15.0
        assert r.latest_price == 20.0
        assert abs(r.premium_pct - (20 / 15 - 1)) < 1e-9
        assert r.state == "profit"

    def test_sell_to_zero_resets(self):
        # 買 100張後全數賣出 → 部位歸零、成本重置 → 無法估算
        r = compute_cost_basis([10.0, 11.0], [100.0, -100.0], state_eps=0.01)
        assert r.latest_cost is None
        assert r.state == "unknown"

    def test_partial_sell_keeps_cost(self):
        # 買 100張@10、賣 50張 → 均價仍 10;現價 12 → 浮盈
        r = compute_cost_basis([10.0, 12.0], [100.0, -50.0], state_eps=0.01)
        assert r.latest_cost == 10.0
        assert r.state == "profit"

    def test_loss_state(self):
        r = compute_cost_basis([20.0, 10.0], [100.0, 0.0], state_eps=0.01)
        assert r.latest_cost == 20.0
        assert r.state == "loss"

    def test_flat_within_eps(self):
        r = compute_cost_basis([100.0, 100.5], [100.0, 0.0], state_eps=0.01)
        assert r.state == "flat"

    def test_costs_aligned_length(self):
        prices = [10.0, None, 12.0]
        nets = [100.0, 50.0, 50.0]
        r = compute_cost_basis(prices, nets, state_eps=0.01)
        assert len(r.costs) == 3
        assert r.costs[0] == 10.0  # 首買
        assert r.costs[1] == 10.0  # price 缺,沿用前值

    def test_length_mismatch_raises(self):
        import pytest

        with pytest.raises(ValueError):
            compute_cost_basis([10.0], [1.0, 2.0], state_eps=0.01)
