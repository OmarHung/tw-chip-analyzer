"""分析服務：FeatureDaily → Chip Score → 決策 → 分析結果。

Phase 1（Daily Scanner）：intraday 特徵尚未有即時來源，以中性值代入；
分數主要反映法人/信用/借券 + TDCC 集中度 + 市場環境。
"""
from __future__ import annotations

from dataclasses import dataclass, replace

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
from app.services.normalize import clamp, cross_sectional_percentile


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

    def _intraday(self, fd: FeatureDaily) -> IntradayFeatures:
        return IntradayFeatures(
            cvd_z=_f(fd.cvd_z),
            large_trade_delta_z=_f(fd.large_trade_delta_z),
            obi=_f(fd.intraday_obi),
            absorption_z=_f(fd.absorption_z),
            trade_speed_z=_f(fd.trade_speed_z),
            price_efficiency_z=_f(fd.price_efficiency_z),
        )

    def _weekly(self, fd: FeatureDaily) -> WeeklyFeatures:
        return WeeklyFeatures(
            large_holder_ratio_change_z=_f(fd.large_holder_ratio_change_z),
            retail_holder_ratio_change_z=_f(fd.retail_holder_ratio_change_z),
            holder_count_change_z=_f(fd.holder_count_change_z),
        )

    def score_features(
        self, fd: FeatureDaily, market: MarketContext | None = None
    ) -> ChipScoreResult:
        """只算 Chip Score(不做決策)。供橫斷面兩段式流程先收集 composite_raw。"""
        market = market or MarketContext()
        # 產業趨勢是逐檔的（依所屬產業），大盤 regime 才是全市場共用；無值則維持中性。
        if fd.industry_trend_score is not None:
            market = replace(
                market, industry_trend_score=float(fd.industry_trend_score)
            )
        # 有當日逐筆的標的 → 四維（含 intraday）；無者維持排除、權重重分配給其餘
        # 成分（OECD 複合指標標準做法，見 docs/03 §10 補充）。
        has_intraday = fd.cvd_z is not None or fd.large_trade_delta_z is not None
        active = {"institutional", "holder", "market"}
        if has_intraday:
            active.add("intraday")
        return self.scorer.score(
            self._intraday(fd) if has_intraday else IntradayFeatures(),
            self._daily(fd),
            self._weekly(fd),
            market,
            active_components=active,
        )

    def analyze(
        self,
        fd: FeatureDaily,
        market: MarketContext | None = None,
        already_in_position: bool = False,
        name: str | None = None,
        chip: ChipScoreResult | None = None,
        chip_score_override: float | None = None,
    ) -> AnalysisResult:
        """單檔完整分析。chip / chip_score_override 供橫斷面百分位流程重入:
        先 score_features 收集全市場 composite_raw → 百分位 → 帶回覆寫分數再決策。
        """
        chip = chip or self.score_features(fd, market)
        if chip_score_override is not None:
            chip = replace(chip, chip_score=chip_score_override)

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


def analyze_market(
    service: AnalysisService,
    items: list[tuple[FeatureDaily, str | None]],
    market: MarketContext | None = None,
) -> list[AnalysisResult]:
    """同一交易日整組標的的分析(單一真相來源:scanner/dashboard/persist/單股共用)。

    scoring.mapping=percentile(預設):兩段式——先逐檔 score_features 收集
    composite_raw,做當日橫斷面百分位(0~100)為 chip_score,再帶回決策。
    只用同日資料,無 look-ahead。mapping=linear 則維持舊制 50+50*composite。

    **百分位依「成分組合」分組計算**:缺成分時權重會重分配,四維(有逐筆)與三維
    (無逐筆)的 composite_raw 尺度不同——實測 intraday 分項均值為中性 0 時,四維的
    raw 恰為三維的 (1 - intraday 權重) 倍(0.077/0.119 = 0.647 = 1 - 0.35)。混在
    同一排名裡,有逐筆的標的高低分都被往中間擠,而「有沒有逐筆」只反映 Shioaji 當天
    抓到誰,與籌碼無關。分組後語意為「當日同類標的中的前 X%」,不因逐筆覆蓋率漂移。
    """
    mapping = service.t.scoring.get("mapping", "linear")
    chips = [service.score_features(fd, market) for fd, _ in items]
    overrides: list[float | None] = [None] * len(chips)
    if mapping == "percentile" and len(chips) > 1:
        groups: dict[frozenset[str], list[int]] = {}
        for i, c in enumerate(chips):
            groups.setdefault(c.components, []).append(i)
        for idxs in groups.values():
            # 單一標的的組無法排名(百分位無意義),退回 linear 映射避免給出假的 0/100
            if len(idxs) < 2:
                continue
            for i, pct in zip(
                idxs, cross_sectional_percentile([chips[i].composite_raw for i in idxs])
            ):
                overrides[i] = pct
    return [
        service.analyze(
            fd, market=market, name=name, chip=chip, chip_score_override=ov
        )
        for (fd, name), chip, ov in zip(items, chips, overrides)
    ]
