"""分析服務：FeatureDaily → Chip Score → 決策 → 分析結果。

Phase 1（Daily Scanner）：intraday 特徵尚未有即時來源，以中性值代入；
分數主要反映法人/信用/借券 + TDCC 集中度 + 市場環境。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Thresholds, get_thresholds
from app.db.models.features import FeatureDaily
from app.models.signal import (
    DailyFeatures,
    IntradayFeatures,
    MarketContext,
    SignalResult,
    WeeklyFeatures,
)
from app.services.chip import ChipScorer, ChipScoreResult
from app.services.decision import PriceContext, decide
from app.services.normalize import clamp


def _f(value, default: float = 0.0) -> float:
    return default if value is None else float(value)


@dataclass
class AnalysisResult:
    symbol: str
    name: str
    price: float
    change_pct: float | None
    chip: ChipScoreResult
    signal: SignalResult


class AnalysisService:
    def __init__(self, thresholds: Thresholds | None = None):
        self.t = thresholds or get_thresholds()
        self.scorer = ChipScorer(self.t)

    def _daily(self, fd: FeatureDaily) -> DailyFeatures:
        return DailyFeatures(
            foreign_5d_z=_f(fd.foreign_5d_z),
            trust_5d_z=_f(fd.trust_5d_z),
            dealer_5d_z=_f(fd.dealer_5d_z),
            margin_balance_change_z=_f(fd.margin_balance_change_z),
            short_balance_change_z=_f(fd.short_balance_change_z),
            sbl_change_z=_f(fd.sbl_change_z),
            close_vs_vwap_pct=_f(fd.close_vs_vwap_pct),
        )

    def _weekly(self, fd: FeatureDaily) -> WeeklyFeatures:
        return WeeklyFeatures(
            large_holder_ratio_change_z=_f(fd.large_holder_ratio_change_z),
            retail_holder_ratio_change_z=_f(fd.retail_holder_ratio_change_z),
            holder_count_change_z=_f(fd.holder_count_change_z),
        )

    def analyze(
        self,
        fd: FeatureDaily,
        market: MarketContext | None = None,
        already_in_position: bool = False,
        name: str | None = None,
    ) -> AnalysisResult:
        market = market or MarketContext()
        # Phase 1 尚無盤中即時資料 → 排除 intraday，權重重分配給其餘成分
        # （OECD 複合指標標準做法，見 docs/03 §10 補充）。
        chip = self.scorer.score(
            IntradayFeatures(),
            self._daily(fd),
            self._weekly(fd),
            market,
            active_components={"institutional", "holder", "market"},
        )

        last_price = _f(fd.close)
        atr14 = _f(fd.atr14, default=max(last_price * 0.02, 0.01))
        swing_low = _f(fd.recent_swing_low, default=last_price * 0.95)

        # market_score(0..100) 轉回 -1..1 供 entry filter 用
        market_norm = clamp((chip.market - 50) / 50)
        price_ctx = PriceContext(
            close_vs_vwap_pct=_f(fd.close_vs_vwap_pct),
            close_vs_ma20_pct=_f(fd.close_vs_ma20_pct),
            turnover=_f(fd.turnover),
            market_score_norm=market_norm,
        )

        signal = decide(
            score=chip.chip_score,
            reasons=chip.reasons,
            last_price=last_price,
            atr14=atr14,
            recent_swing_low=swing_low,
            price_ctx=price_ctx,
            already_in_position=already_in_position,
            thresholds=self.t,
        )
        return AnalysisResult(
            symbol=fd.symbol,
            name=name or fd.symbol,
            price=last_price,
            change_pct=_f(fd.change_pct) if fd.change_pct is not None else None,
            chip=chip,
            signal=signal,
        )
