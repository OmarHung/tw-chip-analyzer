"""Composite Chip Score。見 docs/03 §10-11。

把四個分項（各 -1..1）以 config 權重組合，轉成 0..100，並產生 reasons。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import Thresholds, get_thresholds
from app.models.signal import (
    DailyFeatures,
    IntradayFeatures,
    MarketContext,
    WeeklyFeatures,
)
from app.services.chip.holder_score import holder_score
from app.services.chip.institutional_score import institutional_score
from app.services.chip.intraday_score import intraday_score
from app.services.chip.market_score import market_score
from app.services.normalize import clamp


def to_0_100(sub: float) -> float:
    """把 -1..1 的分項轉成 0..100（50 為中性）。"""
    return round(50 + 50 * clamp(sub), 1)


@dataclass
class ChipScoreResult:
    chip_score: float
    intraday: float
    institutional: float
    holder: float
    market: float
    reasons: list[str] = field(default_factory=list)
    # 加權合成原始值(-1..1),供橫斷面百分位映射(scoring.mapping=percentile)使用
    composite_raw: float = 0.0


class ChipScorer:
    def __init__(self, thresholds: Thresholds | None = None):
        self.t = thresholds or get_thresholds()

    def score(
        self,
        intraday: IntradayFeatures,
        daily: DailyFeatures,
        weekly: WeeklyFeatures,
        market: MarketContext,
        active_components: set[str] | None = None,
    ) -> ChipScoreResult:
        """active_components：本期實際有資料的成分。

        某成分無資料時（例如 Phase 1 尚無盤中 intraday），依 OECD 複合指標
        標準做法排除該成分，並把權重按比例重分配給其餘成分（避免以中性值
        灌水拉低訊號）。預設四項全用。
        """
        w = self.t.weights
        s_intra = intraday_score(intraday, w["intraday"])
        s_inst = institutional_score(daily, w["institutional"])
        s_hold = holder_score(weekly, w["holder"])
        s_mkt = market_score(market, w["market"])

        subscores = {
            "intraday": s_intra,
            "institutional": s_inst,
            "holder": s_hold,
            "market": s_mkt,
        }
        active = active_components or set(subscores)
        cw = w["composite"]
        active_weight = sum(cw[k] for k in active) or 1.0
        composite = sum(
            (cw[k] / active_weight) * subscores[k] for k in active
        )

        return ChipScoreResult(
            chip_score=to_0_100(composite),
            intraday=to_0_100(s_intra),
            institutional=to_0_100(s_inst),
            holder=to_0_100(s_hold),
            market=to_0_100(s_mkt),
            reasons=self._reasons(intraday, daily, weekly),
            composite_raw=composite,
        )

    @staticmethod
    def _reasons(
        intraday: IntradayFeatures, daily: DailyFeatures, weekly: WeeklyFeatures
    ) -> list[str]:
        r: list[str] = []
        if intraday.large_trade_delta_z >= 1.5:
            r.append("盤中大額主動買盤顯著偏強")
        if intraday.cvd_z >= 1.2:
            r.append("CVD 顯示主動買盤持續累積")
        if intraday.absorption_z >= 1.2:
            r.append("低檔承接/賣壓吸收訊號偏強")
        if daily.trust_5d_z >= 1.0:
            r.append("投信近5日買超強度偏高")
        if daily.foreign_5d_z >= 1.0:
            r.append("外資近5日買超強度偏高")
        if daily.margin_balance_change_z <= -1.0:
            r.append("融資下降，有利籌碼沉澱")
        if daily.sbl_change_z >= 1.2:
            r.append("借券賣出增加，存在偏空壓力")
        if weekly.large_holder_ratio_change_z >= 1.0:
            r.append("TDCC 大戶持股比週增幅偏強")
        if weekly.retail_holder_ratio_change_z <= -1.0:
            r.append("散戶持股比下降，籌碼趨向集中")
        return r
