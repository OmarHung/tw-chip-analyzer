"""特徵與訊號快照資料表。"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.mixins import AvailabilityMixin, TimestampMixin


class FeatureDaily(Base, AvailabilityMixin, TimestampMixin):
    """每日聚合特徵（已正規化，供 scoring 使用）。

    以 Z-score / ratio 儲存，跨股票可比較（見 docs/03 §9）。
    """

    __tablename__ = "feature_daily"
    __table_args__ = (UniqueConstraint("symbol", "data_date", name="uq_feature_daily"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(
        String(16), ForeignKey("stock.symbol"), nullable=False, index=True
    )

    # 價格結構
    close: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    change_pct: Mapped[float | None] = mapped_column()  # 當日漲跌幅（對前一交易日）
    atr14: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    ma20: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    vwap: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    recent_swing_low: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    turnover: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    close_vs_ma20_pct: Mapped[float | None] = mapped_column()
    close_vs_vwap_pct: Mapped[float | None] = mapped_column()

    # 法人/信用（正規化 Z-score）
    foreign_5d_z: Mapped[float | None] = mapped_column()
    trust_5d_z: Mapped[float | None] = mapped_column()
    dealer_5d_z: Mapped[float | None] = mapped_column()
    margin_balance_change_z: Mapped[float | None] = mapped_column()
    short_balance_change_z: Mapped[float | None] = mapped_column()
    sbl_change_z: Mapped[float | None] = mapped_column()

    # 產業趨勢（-1..1）：所屬產業成分股近 5 日報酬中位數，跨產業橫斷面 z 後 squash。
    # 產業別來自 stock.industry（MOPS 基本資料）；無產業別/樣本不足者為 NULL（中性）。
    industry_trend_score: Mapped[float | None] = mapped_column()

    # TDCC（週資料，前向填補到日）
    large_holder_ratio_change_z: Mapped[float | None] = mapped_column()
    retail_holder_ratio_change_z: Mapped[float | None] = mapped_column()
    holder_count_change_z: Mapped[float | None] = mapped_column()

    # 盤中 order flow（由當日逐筆計算；無逐筆的標的為 NULL → composite 排除 intraday）。
    # cvd_z / large_trade_delta_z：對「當日有逐筆的標的集合」做橫斷面 Z-score
    # （訊號本身已是 turnover-neutral 比率，滿足跨股票可比較）；
    # intraday_obi：CVD 斜率的每分鐘量正規化值（-1..1），走 clamp 管線。
    cvd_z: Mapped[float | None] = mapped_column()
    large_trade_delta_z: Mapped[float | None] = mapped_column()
    intraday_obi: Mapped[float | None] = mapped_column()
    absorption_z: Mapped[float | None] = mapped_column()
    trade_speed_z: Mapped[float | None] = mapped_column()
    price_efficiency_z: Mapped[float | None] = mapped_column()


class SignalSnapshot(Base, AvailabilityMixin, TimestampMixin):
    """每個訊號的完整快照（含 feature payload），供回測與未來 ML。"""

    __tablename__ = "signal_snapshot"
    __table_args__ = (
        UniqueConstraint("symbol", "data_date", name="uq_signal_snapshot"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(
        String(16), ForeignKey("stock.symbol"), nullable=False, index=True
    )
    chip_score: Mapped[float] = mapped_column(nullable=False)
    intraday_score: Mapped[float | None] = mapped_column()
    institutional_score: Mapped[float | None] = mapped_column()
    holder_score: Mapped[float | None] = mapped_column()
    market_score: Mapped[float | None] = mapped_column()
    action: Mapped[str] = mapped_column(String(8), nullable=False)
    entry_low: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    entry_high: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    stop_loss: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    tp1: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    tp2: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    risk_reward: Mapped[float | None] = mapped_column()
    reasons: Mapped[list | None] = mapped_column(JSON)
    payload: Mapped[dict | None] = mapped_column(JSON)  # 完整 feature 快照
