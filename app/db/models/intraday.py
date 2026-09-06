"""盤中逐筆資料表（見 docs/02 §7 raw_tick）。"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.mixins import TimestampMixin


class RawTick(Base, TimestampMixin):
    """逐筆成交（來源：Shioaji ticks）。aggressor_side：1=買(外盤)、-1=賣(內盤)、0=無法判定。"""

    __tablename__ = "raw_tick"
    __table_args__ = (Index("ix_raw_tick_symbol_date", "symbol", "data_date"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(
        String(16), ForeignKey("stock.symbol"), nullable=False
    )
    data_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    volume: Mapped[int] = mapped_column(Integer, nullable=False)
    bid_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    ask_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    aggressor_side: Mapped[int | None] = mapped_column(SmallInteger)
