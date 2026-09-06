from __future__ import annotations
from app.models.signal import *
from math import tanh

def clamp(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, x))

def squash_z(z: float) -> float:
    # maps roughly z=-3..3 to -1..1
    return tanh(z / 2.0)

class ChipScorer:
    """
    Produces a 0-100 score.
    50 = neutral, >65 constructive, >75 strong.
    """

    def score(
        self,
        intraday: IntradayFeatures,
        daily: DailyFeatures,
        weekly: WeeklyFeatures,
        market: MarketContext,
    ) -> tuple[float, list[str]]:
        reasons: list[str] = []

        intraday_score = (
            0.30 * squash_z(intraday.large_trade_delta_z) +
            0.25 * squash_z(intraday.cvd_z) +
            0.20 * squash_z(intraday.absorption_z) +
            0.10 * clamp(intraday.obi) +
            0.10 * squash_z(intraday.trade_speed_z) +
            0.05 * squash_z(intraday.price_efficiency_z)
        )

        daily_score = (
            0.30 * squash_z(daily.trust_5d_z) +
            0.25 * squash_z(daily.foreign_5d_z) +
            0.10 * squash_z(daily.dealer_5d_z) -
            0.15 * squash_z(daily.margin_balance_change_z) -
            0.10 * squash_z(daily.sbl_change_z) -
            0.10 * squash_z(daily.short_balance_change_z)
        )

        weekly_score = (
            0.60 * squash_z(weekly.large_holder_ratio_change_z) -
            0.25 * squash_z(weekly.retail_holder_ratio_change_z) -
            0.15 * squash_z(weekly.holder_count_change_z)
        )

        context_score = (
            0.65 * clamp(market.market_trend_score) +
            0.35 * clamp(market.industry_trend_score)
        )

        composite = (
            0.35 * intraday_score +
            0.30 * daily_score +
            0.25 * weekly_score +
            0.10 * context_score
        )

        score = round(50 + 50 * clamp(composite), 1)

        if intraday.large_trade_delta_z >= 1.5:
            reasons.append("盤中大額主動買盤顯著偏強")
        if intraday.cvd_z >= 1.2:
            reasons.append("CVD 顯示主動買盤持續累積")
        if intraday.absorption_z >= 1.2:
            reasons.append("低檔承接/賣壓吸收訊號偏強")
        if daily.trust_5d_z >= 1.0:
            reasons.append("投信近5日買超強度偏高")
        if daily.foreign_5d_z >= 1.0:
            reasons.append("外資近5日買超強度偏高")
        if daily.margin_balance_change_z <= -1.0:
            reasons.append("融資下降，有利籌碼沉澱")
        if daily.sbl_change_z >= 1.2:
            reasons.append("借券賣出增加，存在偏空壓力")
        if weekly.large_holder_ratio_change_z >= 1.0:
            reasons.append("TDCC 大戶持股比週增幅偏強")
        if weekly.retail_holder_ratio_change_z <= -1.0:
            reasons.append("散戶持股比下降，籌碼趨向集中")

        return score, reasons
