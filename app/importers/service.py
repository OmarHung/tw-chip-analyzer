"""Import 服務：把解析後的 records upsert 進 DB（冪等，含 FK 主檔保護）。"""
from __future__ import annotations

import datetime as dt

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.chips import (
    InstitutionalDaily,
    MarginDaily,
    SblDaily,
    TdccSummaryWeekly,
    TdccWeekly,
)
from app.db.models.market import CorporateAction, DailyPrice, MarketIndex, Stock
from app.importers import tdcc, tpex, twse
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


async def import_tpex_ohlcv(session: AsyncSession, raw: dict, data_date: dt.date) -> int:
    stocks, prices = tpex.parse_ohlcv(raw, data_date)
    if stocks:
        await upsert_many(session, Stock, stocks, ["symbol"], update_columns=["name", "market"])
    n = await upsert_many(session, DailyPrice, prices, ["symbol", "data_date"])
    await session.commit()
    return n


async def import_tpex_institutional(
    session: AsyncSession, raw: dict, data_date: dt.date
) -> int:
    rows = tpex.parse_institutional(raw, data_date)
    await _ensure_stocks(session, {r["symbol"] for r in rows})
    n = await upsert_many(session, InstitutionalDaily, rows, ["symbol", "data_date"])
    await session.commit()
    return n


async def import_tpex_margin(session: AsyncSession, raw: dict, data_date: dt.date) -> int:
    rows = tpex.parse_margin(raw, data_date)
    await _ensure_stocks(session, {r["symbol"] for r in rows})
    n = await upsert_many(session, MarginDaily, rows, ["symbol", "data_date"])
    await session.commit()
    return n


async def import_sbl(session: AsyncSession, raw: dict, data_date: dt.date) -> int:
    rows = twse.parse_sbl(raw, data_date)
    await _ensure_stocks(session, {r["symbol"] for r in rows})
    n = await upsert_many(session, SblDaily, rows, ["symbol", "data_date"])
    await session.commit()
    return n


async def _import_corporate_actions(
    session: AsyncSession, rows: list[dict], update_columns: list[str] | None = None
) -> int:
    await _ensure_stocks(session, {r["symbol"] for r in rows})
    n = await upsert_many(
        session, CorporateAction, rows, ["symbol", "data_date"], update_columns
    )
    await session.commit()
    return n


async def import_ex_dividend(session: AsyncSession, raw: dict) -> int:
    """除權除息事件（TWT49U）。列內自帶 data_date，故不需傳日期。"""
    return await _import_corporate_actions(session, twse.parse_ex_dividend(raw))


async def import_ex_rights_forecast(session: AsyncSession, raw: dict) -> int:
    """除權息預告表（TWT48U）→ 只補 share_factor（量還原因子）。

    衝突時僅更新 share_factor：這列的價格欄位由 TWT49U 在除權息當日填，兩來源寫同一列
    互不覆蓋（預告表當下還沒有前收/參考價）。
    """
    return await _import_corporate_actions(
        session, twse.parse_ex_rights_forecast(raw), ["share_factor"]
    )


async def import_par_change(session: AsyncSession, raw: dict) -> int:
    """面額變更/拆股（TWTB8U）→ CorporateAction。"""
    return await _import_corporate_actions(
        session, twse.parse_resume_reference(raw, "面額")
    )


async def import_capital_reduction(session: AsyncSession, raw: dict) -> int:
    """減資（TWTAUU）→ CorporateAction。"""
    return await _import_corporate_actions(
        session, twse.parse_resume_reference(raw, "減資")
    )


async def import_tpex_ex_dividend(session: AsyncSession, raw: dict) -> int:
    """TPEx 除權除息（exDailyQ）→ CorporateAction（含 share_factor,歷史可回補）。"""
    return await _import_corporate_actions(session, tpex.parse_ex_dividend(raw))


async def import_tpex_par_change(session: AsyncSession, raw: dict) -> int:
    """TPEx 面額變更（pvChgRslt）→ CorporateAction。"""
    return await _import_corporate_actions(
        session, tpex.parse_resume_reference(raw, "面額")
    )


async def import_tpex_capital_reduction(session: AsyncSession, raw: dict) -> int:
    """TPEx 減資（revivt）→ CorporateAction。"""
    return await _import_corporate_actions(
        session, tpex.parse_resume_reference(raw, "減資")
    )


async def import_index(session: AsyncSession, raw: dict) -> int:
    """匯入一個月的 TAIEX 日線（FMTQIK）。"""
    rows = twse.parse_index(raw)
    n = await upsert_many(session, MarketIndex, rows, ["data_date"])
    await session.commit()
    return n


async def import_tdcc(session: AsyncSession, records: list[dict]) -> tuple[int, int]:
    """回傳 (weekly 筆數, summary 筆數)。"""
    _date, weekly, summary = tdcc.parse_distribution(records)
    await _ensure_stocks(session, {r["symbol"] for r in summary})
    nw = await upsert_many(session, TdccWeekly, weekly, ["symbol", "data_date", "level"])
    ns = await upsert_many(
        session, TdccSummaryWeekly, summary, ["symbol", "data_date"]
    )
    await session.commit()
    return nw, ns
