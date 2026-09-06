"""盤後籌碼資料表：法人、信用交易、借券、TDCC 股權分散。"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import BigInteger, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.mixins import AvailabilityMixin, TimestampMixin


class InstitutionalDaily(Base, AvailabilityMixin, TimestampMixin):
    """三大法人買賣超（自營商兩類必須拆開，見 docs/02 §7）。單位：股數，買超為正。"""

    __tablename__ = "institutional_daily"
    __table_args__ = (
        UniqueConstraint("symbol", "data_date", name="uq_institutional_daily"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(
        String(16), ForeignKey("stock.symbol"), nullable=False, index=True
    )
    foreign_net: Mapped[int | None] = mapped_column(BigInteger)  # 外資及陸資
    trust_net: Mapped[int | None] = mapped_column(BigInteger)  # 投信
    dealer_self_net: Mapped[int | None] = mapped_column(BigInteger)  # 自營商(自行買賣)
    dealer_hedge_net: Mapped[int | None] = mapped_column(BigInteger)  # 自營商(避險)


class MarginDaily(Base, AvailabilityMixin, TimestampMixin):
    """融資融券。單位：張。"""

    __tablename__ = "margin_daily"
    __table_args__ = (UniqueConstraint("symbol", "data_date", name="uq_margin_daily"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(
        String(16), ForeignKey("stock.symbol"), nullable=False, index=True
    )
    margin_buy: Mapped[int | None] = mapped_column(Integer)  # 融資買進
    margin_sell: Mapped[int | None] = mapped_column(Integer)  # 融資賣出
    margin_balance: Mapped[int | None] = mapped_column(Integer)  # 融資餘額
    short_sell: Mapped[int | None] = mapped_column(Integer)  # 融券賣出
    short_cover: Mapped[int | None] = mapped_column(Integer)  # 融券回補
    short_balance: Mapped[int | None] = mapped_column(Integer)  # 融券餘額


class SblDaily(Base, AvailabilityMixin, TimestampMixin):
    """借券（Securities Borrowing and Lending）。單位：股數。"""

    __tablename__ = "sbl_daily"
    __table_args__ = (UniqueConstraint("symbol", "data_date", name="uq_sbl_daily"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(
        String(16), ForeignKey("stock.symbol"), nullable=False, index=True
    )
    sbl_short_sell: Mapped[int | None] = mapped_column(BigInteger)  # 借券賣出
    sbl_return: Mapped[int | None] = mapped_column(BigInteger)  # 還券
    sbl_balance: Mapped[int | None] = mapped_column(BigInteger)  # 借券餘額


class TdccWeekly(Base, AvailabilityMixin, TimestampMixin):
    """TDCC 股權分散原始級距（每週一筆一級距）。"""

    __tablename__ = "tdcc_weekly"
    __table_args__ = (
        UniqueConstraint("symbol", "data_date", "level", name="uq_tdcc_weekly"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(
        String(16), ForeignKey("stock.symbol"), nullable=False, index=True
    )
    level: Mapped[int] = mapped_column(Integer, nullable=False)  # 級距序號 1..15
    holder_count: Mapped[int | None] = mapped_column(Integer)  # 人數
    shares: Mapped[int | None] = mapped_column(BigInteger)  # 股數
    ratio: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))  # 佔比 %


class TdccSummaryWeekly(Base, AvailabilityMixin, TimestampMixin):
    """TDCC 聚合（依 config 級距切散戶/中戶/大戶/超大戶）。"""

    __tablename__ = "tdcc_summary_weekly"
    __table_args__ = (
        UniqueConstraint("symbol", "data_date", name="uq_tdcc_summary_weekly"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(
        String(16), ForeignKey("stock.symbol"), nullable=False, index=True
    )
    retail_ratio: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    medium_ratio: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    large_ratio: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    super_large_ratio: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    holder_count: Mapped[int | None] = mapped_column(Integer)
