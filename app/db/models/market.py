"""市場/價格相關資料表。"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import BigInteger, Date, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.mixins import AvailabilityMixin, TimestampMixin


class Stock(Base, TimestampMixin):
    """上市/上櫃股票基本資料。"""

    __tablename__ = "stock"

    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    market: Mapped[str] = mapped_column(String(8), nullable=False)  # TWSE / TPEX
    industry: Mapped[str | None] = mapped_column(String(32))
    shares_outstanding: Mapped[int | None] = mapped_column(BigInteger)


class MarketIndex(Base, AvailabilityMixin, TimestampMixin):
    """大盤加權指數（TAIEX）日線。"""

    __tablename__ = "market_index"
    __table_args__ = (UniqueConstraint("data_date", name="uq_market_index"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    taiex_close: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    turnover: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))


class MarketDaily(Base, AvailabilityMixin, TimestampMixin):
    """每日大盤脈絡（由 MarketIndex + 全市場漲跌家數計算）。"""

    __tablename__ = "market_daily"
    __table_args__ = (UniqueConstraint("data_date", name="uq_market_daily"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    taiex_close: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    taiex_ma20: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    taiex_ma60: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    ma20_slope: Mapped[float | None] = mapped_column()
    advancers: Mapped[int | None] = mapped_column(BigInteger)
    decliners: Mapped[int | None] = mapped_column(BigInteger)
    volatility_pct: Mapped[float | None] = mapped_column()
    market_trend_score: Mapped[float | None] = mapped_column()  # -1..1


class DailyPrice(Base, AvailabilityMixin, TimestampMixin):
    """日 OHLCV（含成交金額，供流動性/量比）。

    注意：close 為 TWSE 原始（未還原）收盤價。除權息／拆股的還原因子存於
    CorporateAction，需連續價格序列（報酬/MA/ATR、回測）時據以調整。
    """

    __tablename__ = "daily_price"
    __table_args__ = (UniqueConstraint("symbol", "data_date", name="uq_daily_price"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(
        String(16), ForeignKey("stock.symbol"), nullable=False, index=True
    )
    open: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    high: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    low: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    close: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    volume: Mapped[int | None] = mapped_column(BigInteger)  # 股數
    turnover: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))  # 成交金額 TWD


class CorporateAction(Base, AvailabilityMixin, TimestampMixin):
    """公司行動造成的價格斷點事件。來源（皆 TWSE，欄位含前收/參考價）：
    - TWT49U 除權除息（kind：權/息/權息）
    - TWTB8U 變更股票面額（拆股/面額變更，kind：面額）
    - TWTAUU 減資恢復買賣（kind：減資）

    價格序列在 data_date（除權息日 / 恢復買賣首日）出現非交易性斷點。還原因子
    `adj_factor = reference_price / prev_close`（配息/拆股 <1、減資 >1）：把 data_date
    之前的價格全部乘上此因子，即可讓報酬/MA/ATR 連續。
    """

    __tablename__ = "corporate_action"
    __table_args__ = (
        UniqueConstraint("symbol", "data_date", name="uq_corporate_action"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(
        String(16), ForeignKey("stock.symbol"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(8), nullable=False)  # 權 / 息 / 權息
    prev_close: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))  # 除權息前收盤價
    reference_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))  # 除權息參考價
    value: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))  # 權值+息值
    adj_factor: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))  # 參考價/前收
