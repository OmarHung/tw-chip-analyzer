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
    """日 OHLCV（含成交金額，供流動性/量比）。"""

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
