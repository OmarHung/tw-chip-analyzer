"""全市場掃描共用邏輯（scanner 與 dashboard 共用）。"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.features import FeatureDaily
from app.db.models.market import Stock
from app.repositories.features import FeatureDailyRepository
from app.repositories.market import load_market_context
from app.services.analysis import AnalysisService


@dataclass
class ScanRow:
    symbol: str
    name: str
    price: float
    change_pct: float | None
    chip_score: float
    intraday: float
    institutional: float
    holder: float
    market: float
    action: str
    turnover: float
    rr: float | None
    industry: str | None


async def scan_all(session: AsyncSession) -> tuple[dt.date | None, list[ScanRow]]:
    """對最新交易日的所有標的產生分析列（未過濾、未排序）。"""
    repo = FeatureDailyRepository(session)
    as_of = await repo.latest_date()
    if as_of is None:
        return None, []

    stmt = (
        select(FeatureDaily, Stock.name, Stock.industry)
        .join(Stock, Stock.symbol == FeatureDaily.symbol)
        .where(FeatureDaily.data_date == as_of)
    )
    service = AnalysisService()
    market = await load_market_context(session, as_of)
    rows: list[ScanRow] = []
    for fd, name, industry in (await session.execute(stmt)).all():
        r = service.analyze(fd, market=market, name=name)
        rows.append(
            ScanRow(
                symbol=r.symbol,
                name=r.name,
                price=r.price,
                change_pct=r.change_pct,
                chip_score=r.chip.chip_score,
                intraday=r.chip.intraday,
                institutional=r.chip.institutional,
                holder=r.chip.holder,
                market=r.chip.market,
                action=r.signal.action.value,
                turnover=float(fd.turnover or 0),
                rr=r.signal.risk_reward,
                industry=industry,
            )
        )
    return as_of, rows
