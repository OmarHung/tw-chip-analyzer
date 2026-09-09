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

    def test_reweight_excludes_missing_component(self):
        """排除 intraday 後，強法人/大戶不應被中性 intraday 灌水拉低。"""
        intra = IntradayFeatures()  # 中性
        daily = DailyFeatures(foreign_5d_z=2.5, trust_5d_z=2.5)
        weekly = WeeklyFeatures(large_holder_ratio_change_z=2.5)
        mkt = MarketContext()
        full = ChipScorer().score(intra, daily, weekly, mkt)
        reweighted = ChipScorer().score(
            intra, daily, weekly, mkt,
            active_components={"institutional", "holder", "market"},
        )
        assert reweighted.chip_score > full.chip_score

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


class TestPercentileMapping:
    """散度修復:橫斷面百分位映射(cross_sectional_percentile)。"""

    def test_preserves_order_and_spreads(self):
        from app.services.normalize import cross_sectional_percentile

        raws = [0.02, -0.05, 0.30, 0.0, 0.11]
        pcts = cross_sectional_percentile(raws)
        # 保序(rank-preserving)
        import numpy as np
        assert np.argsort(raws).tolist() == np.argsort(pcts).tolist()
        # 分布展開:最強者應落在高分區(>=75),最弱者低分區(<=25)
        assert max(pcts) >= 75 and min(pcts) <= 25

    def test_ties_get_average_rank(self):
        from app.services.normalize import cross_sectional_percentile

        pcts = cross_sectional_percentile([0.0, 0.0, 0.0, 0.0])
        assert all(p == 50.0 for p in pcts)  # 全中性 → 全 50

    def test_analyze_market_percentile_spreads_scores(self):
        """analyze_market(percentile)對強弱不同的標的應給出展開的 0~100 分。"""
        from app.db.models.features import FeatureDaily
        from app.services.analysis import AnalysisService, analyze_market

        def fd(sym: str, z: float) -> FeatureDaily:
            return FeatureDaily(symbol=sym, foreign_5d_z=z, trust_5d_z=z, close=100)

        items = [(fd(f"S{i}", z), None) for i, z in enumerate([-2.0, -0.5, 0.0, 0.8, 2.5])]
        results = analyze_market(AnalysisService(), items)
        scores = [r.chip.chip_score for r in results]
        assert scores == sorted(scores)  # 保序
        assert scores[-1] >= 75 and scores[0] <= 25  # 高低分 bucket 有人


class TestPercentileGroupedByComponents:
    """百分位映射依成分組合分組（四維/三維混排會造成結構性偏差）。"""

    @staticmethod
    def _fd(symbol: str, foreign: float, with_intraday: bool):
        import datetime as dt

        from app.db.models.features import FeatureDaily

        return FeatureDaily(
            symbol=symbol, data_date=dt.date(2026, 9, 4),
            available_at=dt.datetime(2026, 9, 4, 15, 0),
            close=100, atr14=2, ma20=100, vwap=100, recent_swing_low=95,
            turnover=5e8, close_vs_ma20_pct=0.0, close_vs_vwap_pct=0.0,
            foreign_5d_z=foreign,
            cvd_z=0.0 if with_intraday else None,
            large_trade_delta_z=0.0 if with_intraday else None,
            intraday_obi=0.0 if with_intraday else None,
            absorption_z=0.0 if with_intraday else None,
            trade_speed_z=0.0 if with_intraday else None,
            price_efficiency_z=0.0 if with_intraday else None,
        )

    def test_each_component_group_spans_full_percentile_range(self):
        """有逐筆與無逐筆各自佔滿 0~100，不因中性 intraday 被稀釋而擠向中間。"""
        from app.services.analysis import AnalysisService, analyze_market

        svc = AnalysisService()
        # 兩組各 5 檔、強度相同；有逐筆組的 intraday 全為中性 0
        items = [
            (self._fd(f"N{i}", z, False), None)
            for i, z in enumerate([-2.0, -1.0, 0.0, 1.0, 2.0])
        ] + [
            (self._fd(f"T{i}", z, True), None)
            for i, z in enumerate([-2.0, -1.0, 0.0, 1.0, 2.0])
        ]
        res = {r.symbol: r.chip.chip_score for r in analyze_market(svc, items)}
        # 兩組各自佔滿同一段百分位（5 檔 → 10/30/50/70/90）
        for prefix in ("N", "T"):
            assert sorted(res[f"{prefix}{i}"] for i in range(5)) == [
                10.0, 30.0, 50.0, 70.0, 90.0
            ], prefix
        # 同名次跨組同分：分數只反映組內排名，與有無逐筆無關
        for i in range(5):
            assert res[f"N{i}"] == res[f"T{i}"]

    def test_missing_tdcc_excludes_holder_component(self):
        """無 TDCC 快照 → holder 成分被排除（不以中性值灌水），並自成一個映射組。"""
        from app.services.analysis import AnalysisService

        svc = AnalysisService()
        with_tdcc = self._fd("A", 1.0, False)
        with_tdcc.large_holder_ratio_change_z = 0.5
        without = self._fd("B", 1.0, False)
        without.large_holder_ratio_change_z = None

        assert "holder" in svc.score_features(with_tdcc).components
        assert "holder" not in svc.score_features(without).components
