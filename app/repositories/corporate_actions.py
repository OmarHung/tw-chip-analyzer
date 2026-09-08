"""公司行動事件讀取：供 feature_builder / backtest 做還原價與還原量。"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.market import CorporateAction

Factors = dict[str, list[tuple[dt.date, float]]]


async def load_factors(
    session: AsyncSession,
    symbols: list[str] | None = None,
    start: dt.date | None = None,
    end: dt.date | None = None,
) -> tuple[Factors, Factors]:
    """回傳 (價格因子, 股數因子)，皆為 {symbol: [(ex_date, factor)]}。

    價格因子 = adj_factor（除權息/拆股/減資的價格斷點）；股數因子 = share_factor
    （只有股數真的變動才 ≠1）。None 與 1.0 一律略過（乘 1 無作用），故純除息股不會
    出現在股數因子 map 裡。symbols=None 表全市場；start/end 限制 ex_date 區間（含）。
    """
    stmt = select(
        CorporateAction.symbol,
        CorporateAction.data_date,
        CorporateAction.adj_factor,
        CorporateAction.share_factor,
    )
    if symbols is not None:
        stmt = stmt.where(CorporateAction.symbol.in_(symbols))
    if start is not None:
        stmt = stmt.where(CorporateAction.data_date >= start)
    if end is not None:
        stmt = stmt.where(CorporateAction.data_date <= end)

    price: Factors = defaultdict(list)
    share: Factors = defaultdict(list)
    for sym, d, adj, sf in (await session.execute(stmt)).all():
        if adj is not None:
            price[sym].append((d, float(adj)))
        if sf is not None and float(sf) != 1.0:
            share[sym].append((d, float(sf)))
    return dict(price), dict(share)


async def load_actions(
    session: AsyncSession,
    symbols: list[str] | None = None,
    start: dt.date | None = None,
    end: dt.date | None = None,
) -> Factors:
    """只要價格還原因子時的捷徑（backtest 用）：{symbol: [(ex_date, adj_factor)]}。"""
    price, _ = await load_factors(session, symbols, start, end)
    return price
