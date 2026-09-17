"""匯總所有 ORM model，讓 Base.metadata 完整（Alembic autogenerate 需要）。"""
from app.db.models.auth import User, UserSession
from app.db.models.chips import (
    InstitutionalDaily,
    MarginDaily,
    SblDaily,
    TdccSummaryWeekly,
    TdccWeekly,
)
from app.db.models.features import FeatureDaily, SignalSnapshot
from app.db.models.intraday import RawTick
from app.db.models.jobs import JobRun
from app.db.models.mops import (
    InsiderHoldingMonthly,
    InsiderTransferDeclaration,
    MopsFetchCoverage,
    MopsShadowFeatureDaily,
)
from app.db.models.settings import NotifySetting, ThresholdChange, ThresholdOverride
from app.db.models.market import (
    CorporateAction,
    DailyPrice,
    FuturesDaily,
    MarketDaily,
    MarketIndex,
    Stock,
)

__all__ = [
    "User",
    "UserSession",
    "Stock",
    "DailyPrice",
    "CorporateAction",
    "MarketIndex",
    "MarketDaily",
    "FuturesDaily",
    "RawTick",
    "InstitutionalDaily",
    "MarginDaily",
    "SblDaily",
    "TdccWeekly",
    "TdccSummaryWeekly",
    "FeatureDaily",
    "SignalSnapshot",
    "NotifySetting",
    "ThresholdOverride",
    "ThresholdChange",
    "JobRun",
    "InsiderHoldingMonthly",
    "InsiderTransferDeclaration",
    "MopsFetchCoverage",
    "MopsShadowFeatureDaily",
]
