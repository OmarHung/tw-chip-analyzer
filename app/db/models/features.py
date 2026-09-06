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

    # TDCC（週資料，前向填補到日）
    large_holder_ratio_change_z: Mapped[float | None] = mapped_column()
    retail_holder_ratio_change_z: Mapped[float | None] = mapped_column()
    holder_count_change_z: Mapped[float | None] = mapped_column()


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
