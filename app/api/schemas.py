"""API response schema（對應 docs/05 §15）。"""
from __future__ import annotations

from pydantic import BaseModel

from app.services.analysis import AnalysisResult


class Scores(BaseModel):
    intraday: float
    institutional: float
    holder: float
    market: float


class EntryZone(BaseModel):
    low: float
    high: float


class RiskInfo(BaseModel):
    stop_loss: float | None = None
    tp1: float | None = None
    tp2: float | None = None
    rr: float | None = None


class AnalysisResponse(BaseModel):
    symbol: str
    name: str
    market: str | None = None  # TWSE(上市) / TPEx(上櫃);由 endpoint 補,不在 AnalysisResult
    industry: str | None = None  # 產業別(MOPS 中文名);同上由 endpoint 補
    price: float
    change_pct: float | None = None
    chip_score: float
    scores: Scores
    action: str
    entry: EntryZone | None = None
    risk: RiskInfo
    reasons: list[str]

    @classmethod
    def from_result(cls, r: AnalysisResult) -> "AnalysisResponse":
        s = r.signal
        entry = (
            EntryZone(low=s.entry_zone[0], high=s.entry_zone[1])
            if s.entry_zone
            else None
        )
        return cls(
            symbol=r.symbol,
            name=r.name,
            price=r.price,
            change_pct=r.change_pct,
            chip_score=r.chip.chip_score,
            scores=Scores(
                intraday=r.chip.intraday,
                institutional=r.chip.institutional,
                holder=r.chip.holder,
                market=r.chip.market,
            ),
            action=s.action.value,
            entry=entry,
            risk=RiskInfo(
                stop_loss=s.stop_loss, tp1=s.take_profit_1, tp2=s.take_profit_2, rr=s.risk_reward
            ),
            reasons=s.reasons,
        )


class ScannerRow(BaseModel):
    symbol: str
    name: str
    price: float
    change_pct: float | None = None
    chip_score: float
    intraday: float
    institutional: float
    holder: float
    action: str
    turnover: float
    rr: float | None = None


class ScannerResponse(BaseModel):
    as_of: str | None = None
    count: int
    rows: list[ScannerRow]
