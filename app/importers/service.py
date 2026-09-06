"""Import 服務：把解析後的 records upsert 進 DB（冪等，含 FK 主檔保護）。"""
from __future__ import annotations

import datetime as dt

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.chips import InstitutionalDaily, MarginDaily
from app.db.models.market import DailyPrice, Stock
from app.importers import twse
from app.repositories.upsert import upsert_ignore, upsert_many


async def _ensure_stocks(session: AsyncSession, symbols: set[str]) -> None:
    """為子表 FK 補上缺少的 Stock stub（name 暫用 symbol，不覆蓋既有）。"""
    stubs = [{"symbol": s, "name": s, "market": "TWSE"} for s in symbols]
    await upsert_ignore(session, Stock, stubs, ["symbol"])


async def import_ohlcv(session: AsyncSession, raw: dict, data_date: dt.date) -> int:
    stocks, prices = twse.parse_ohlcv(raw, data_date)
    if stocks:
        await upsert_many(session, Stock, stocks, ["symbol"], update_columns=["name", "market"])
    n = await upsert_many(session, DailyPrice, prices, ["symbol", "data_date"])
    await session.commit()
    return n


async def import_institutional(
    session: AsyncSession, raw: dict, data_date: dt.date
) -> int:
    rows = twse.parse_institutional(raw, data_date)
    await _ensure_stocks(session, {r["symbol"] for r in rows})
    n = await upsert_many(session, InstitutionalDaily, rows, ["symbol", "data_date"])
    await session.commit()
    return n


async def import_margin(session: AsyncSession, raw: dict, data_date: dt.date) -> int:
    rows = twse.parse_margin(raw, data_date)
    await _ensure_stocks(session, {r["symbol"] for r in rows})
    n = await upsert_many(session, MarginDaily, rows, ["symbol", "data_date"])
    await session.commit()
    return n
