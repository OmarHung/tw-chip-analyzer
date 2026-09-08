"""除權除息事件讀取：供 feature_builder / backtest 做還原價。"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.market import CorporateAction


async def load_actions(
    session: AsyncSession,
    symbols: list[str] | None = None,
    start: dt.date | None = None,
    end: dt.date | None = None,
) -> dict[str, list[tuple[dt.date, float]]]:
    """回傳 {symbol: [(ex_date, adj_factor)]}，adj_factor 為 None 者略過。

    symbols=None 表全市場；start/end 限制 ex_date 區間（含）。
    """
    stmt = select(
        CorporateAction.symbol, CorporateAction.data_date, CorporateAction.adj_factor
    )
    if symbols is not None:
        stmt = stmt.where(CorporateAction.symbol.in_(symbols))
    if start is not None:
        stmt = stmt.where(CorporateAction.data_date >= start)
    if end is not None:
        stmt = stmt.where(CorporateAction.data_date <= end)

    out: dict[str, list[tuple[dt.date, float]]] = defaultdict(list)
    for sym, d, factor in (await session.execute(stmt)).all():
        if factor is not None:
            out[sym].append((d, float(factor)))
    return dict(out)
