"""UI 觸發的腳本執行紀錄（系統頁「工作」）。"""
from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class JobRun(Base):
    __tablename__ = "job_run"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    # running / succeeded / failed / cancelled / interrupted（API 重啟時仍在跑）
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    exit_code: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(256), nullable=False)
    # 啟動當下的設定資料版本；重建類工作用來判斷「分數是否已依目前設定重建」
    data_version: Mapped[str | None] = mapped_column(String(32))
    output: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
