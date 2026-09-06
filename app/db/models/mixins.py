"""共用欄位 mixin。"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import Date, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AvailabilityMixin:
    """Look-ahead bias 防護（見 docs/06 §18）。

    - data_date：資料所描述的交易日/週。
    - available_at：這筆資料實際「可被使用」的時點（公布時間）。
    Backtest 必須依 available_at 判斷當時是否可用。
    """

    data_date: Mapped[dt.date] = mapped_column(Date, nullable=False, index=True)
    available_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
