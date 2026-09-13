"""門檻設定覆寫與修改歷史（YAML 為預設，DB 只存 UI 覆寫值，見 app/core/threshold_registry）。"""
from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ThresholdOverride(Base):
    """覆寫值：key 為 YAML 路徑（如 signal.buy_score），不在登錄表的殘留鍵會被忽略並回報。"""

    __tablename__ = "threshold_override"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ThresholdChange(Base):
    """修改歷史（含重設）。old/new 為 NULL 代表「無覆寫＝YAML 預設」。"""

    __tablename__ = "threshold_change"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    old_value: Mapped[Any | None] = mapped_column(JSON)
    new_value: Mapped[Any | None] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(256), nullable=False)
    data_version: Mapped[str] = mapped_column(String(32), nullable=False)
    changed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
