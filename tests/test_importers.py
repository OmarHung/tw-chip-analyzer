"""Importer parser + DB 匯入測試（用 fixtures，不打外部 API）。"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from sqlalchemy import func, select

from app.db.models.chips import InstitutionalDaily, MarginDaily
from app.db.models.market import DailyPrice, Stock
from app.importers import twse
from app.importers.base import is_stock_symbol, parse_float, parse_int
from app.importers.service import (
    import_institutional,
    import_margin,
    import_ohlcv,
)

FIX = Path(__file__).parent / "fixtures"
D = dt.date(2025, 9, 3)


def _load(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


class TestBase:
    def test_parse_int(self):
        assert parse_int("1,262,000") == 1_262_000
        assert parse_int("--") is None
        assert parse_int("") is None
        assert parse_int(None) is None

    def test_parse_float(self):
        assert parse_float("51.90") == 51.90
        assert parse_float("X") is None

    def test_is_stock_symbol(self):
        assert is_stock_symbol("2330")
        assert not is_stock_symbol("0050")  # ETF
        assert not is_stock_symbol("00715L")
        assert not is_stock_symbol("　")


class TestParsers:
    def test_ohlcv(self):
        stocks, prices = twse.parse_ohlcv(_load("twse_mi_index.json"), D)
        assert len(prices) == len(stocks) >= 1
        p = prices[0]
        assert p["data_date"] == D
        assert p["close"] is not None and p["open"] is not None
        assert p["turnover"] is not None
        assert all(is_stock_symbol(s["symbol"]) for s in stocks)

    def test_institutional_foreign_combined(self):
        rows = twse.parse_institutional(_load("twse_t86.json"), D)
        assert rows
        r = rows[0]
        assert set(r) >= {"foreign_net", "trust_net", "dealer_self_net", "dealer_hedge_net"}
        # 自營商兩類必須分開存在
        assert "dealer_self_net" in r and "dealer_hedge_net" in r

    def test_margin_positions(self):
        rows = twse.parse_margin(_load("twse_mi_margn.json"), D)
        assert rows
        r = rows[0]
        assert set(r) >= {"margin_balance", "short_balance", "margin_buy", "short_sell"}


class TestImportToDB:
    async def test_import_ohlcv_and_children(self, db_session):
        n = await import_ohlcv(db_session, _load("twse_mi_index.json"), D)
        assert n >= 1
        # 主檔與價格都寫入
        assert (await db_session.execute(select(func.count()).select_from(Stock))).scalar() >= 1
        assert (await db_session.execute(select(func.count()).select_from(DailyPrice))).scalar() == n

        # 法人與融資：即使主檔缺也會補 stub（FK 安全）
        ni = await import_institutional(db_session, _load("twse_t86.json"), D)
        nm = await import_margin(db_session, _load("twse_mi_margn.json"), D)
        assert ni >= 1 and nm >= 1
        assert (await db_session.execute(select(func.count()).select_from(InstitutionalDaily))).scalar() == ni
        assert (await db_session.execute(select(func.count()).select_from(MarginDaily))).scalar() == nm

    async def test_import_is_idempotent(self, db_session):
        await import_ohlcv(db_session, _load("twse_mi_index.json"), D)
        first = (await db_session.execute(select(func.count()).select_from(DailyPrice))).scalar()
        await import_ohlcv(db_session, _load("twse_mi_index.json"), D)  # 重跑
        second = (await db_session.execute(select(func.count()).select_from(DailyPrice))).scalar()
        assert first == second  # 不重複
