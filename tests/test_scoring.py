"""里程碑 C：Chip Score 引擎測試（見 docs/03 §10-11）。"""
from __future__ import annotations

from app.models.signal import (
    DailyFeatures,
    IntradayFeatures,
    MarketContext,
    WeeklyFeatures,
)
from app.services.chip import ChipScorer
from app.services.normalize import percentile_rank, rolling_zscore, squash_z


def _bullish():
    return (
        IntradayFeatures(
            cvd_z=1.8, large_trade_delta_z=2.1, obi=0.28,
            absorption_z=1.3, trade_speed_z=1.0, price_efficiency_z=0.4,
        ),
        DailyFeatures(
            foreign_5d_z=0.8, trust_5d_z=1.4, dealer_5d_z=0.2,
            margin_balance_change_z=-1.0, short_balance_change_z=0.1, sbl_change_z=0.3,
        ),
        WeeklyFeatures(
            large_holder_ratio_change_z=1.3,
            retail_holder_ratio_change_z=-1.1,
            holder_count_change_z=-0.8,
        ),
        MarketContext(market_trend_score=0.4, industry_trend_score=0.6),
    )


def _bearish():
    return (
        IntradayFeatures(cvd_z=-1.8, large_trade_delta_z=-2.0, obi=-0.3, absorption_z=-1.0),
        DailyFeatures(foreign_5d_z=-1.2, trust_5d_z=-1.5, margin_balance_change_z=1.5, sbl_change_z=1.5),
        WeeklyFeatures(large_holder_ratio_change_z=-1.2, retail_holder_ratio_change_z=1.2),
        MarketContext(market_trend_score=-0.6, industry_trend_score=-0.5),
    )


class TestNormalize:
    def test_squash_monotonic(self):
        assert squash_z(-3) < squash_z(0) < squash_z(3)
        assert squash_z(0) == 0.0

    def test_rolling_zscore(self):
        z = rolling_zscore([10, 10, 10, 10], 10)
        assert abs(z) < 1e-6
        assert rolling_zscore([1, 2, 3, 4, 5], 5) > 0

    def test_percentile_rank(self):
        assert percentile_rank([1, 2, 3, 4], 4) == 1.0
        assert percentile_rank([], 5) == 0.5


class TestChipScorer:
    def test_range_0_100(self):
        scorer = ChipScorer()
        r = scorer.score(*_bullish())
        assert 0 <= r.chip_score <= 100
        for s in (r.intraday, r.institutional, r.holder, r.market):
            assert 0 <= s <= 100

    def test_bullish_above_neutral(self):
        r = ChipScorer().score(*_bullish())
        assert r.chip_score > 50
        assert r.reasons  # 應產生轉強原因

    def test_bearish_below_neutral(self):
        r = ChipScorer().score(*_bearish())
        assert r.chip_score < 50

    def test_neutral_is_50(self):
        r = ChipScorer().score(
            IntradayFeatures(), DailyFeatures(), WeeklyFeatures(), MarketContext()
        )
        assert r.chip_score == 50.0

    def test_monotonic_stronger_input_higher_score(self):
        """核心成功標準的雛形：越強的輸入 → 越高的分數。"""
        weak = ChipScorer().score(
            IntradayFeatures(cvd_z=0.5, large_trade_delta_z=0.5),
            DailyFeatures(trust_5d_z=0.5), WeeklyFeatures(), MarketContext(),
        )
        strong = ChipScorer().score(
            IntradayFeatures(cvd_z=2.5, large_trade_delta_z=2.5),
            DailyFeatures(trust_5d_z=2.5), WeeklyFeatures(), MarketContext(),
        )
        assert strong.chip_score > weak.chip_score
