"""匯總所有 ORM model，讓 Base.metadata 完整（Alembic autogenerate 需要）。"""
from app.db.models.chips import (
    InstitutionalDaily,
    MarginDaily,
    SblDaily,
    TdccSummaryWeekly,
    TdccWeekly,
)
from app.db.models.features import FeatureDaily, SignalSnapshot
from app.db.models.market import DailyPrice, Stock

__all__ = [
    "Stock",
    "DailyPrice",
    "InstitutionalDaily",
    "MarginDaily",
    "SblDaily",
    "TdccWeekly",
    "TdccSummaryWeekly",
    "FeatureDaily",
    "SignalSnapshot",
]
