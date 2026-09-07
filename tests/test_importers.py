"""Importer parser + DB 匯入測試（用 fixtures，不打外部 API）。"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from sqlalchemy import func, select

from app.db.models.chips import InstitutionalDaily, MarginDaily, SblDaily
from app.db.models.market import DailyPrice, Stock
from app.importers import twse
from app.importers.base import is_stock_symbol, parse_float, parse_int
from app.importers.service import (
    import_institutional,
    import_margin,
    import_ohlcv,
    import_sbl,
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

    def test_sbl_positions_and_filter(self):
        rows = twse.parse_sbl(_load("twse_twt93u.json"), D)
        assert rows
        # ETF/00 開頭代碼被過濾（fixture 含 00400A）
        assert all(is_stock_symbol(r["symbol"]) for r in rows)
        assert not any(r["symbol"] == "00400A" for r in rows)
        # 借券段固定位置正確：2330 賣出 2,000 / 還券 38,000 / 餘額 15,994,514
        r2330 = next(r for r in rows if r["symbol"] == "2330")
        assert r2330["sbl_short_sell"] == 2_000
        assert r2330["sbl_return"] == 38_000
        assert r2330["sbl_balance"] == 15_994_514


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
        ns = await import_sbl(db_session, _load("twse_twt93u.json"), D)
        assert ni >= 1 and nm >= 1 and ns >= 1
        assert (await db_session.execute(select(func.count()).select_from(InstitutionalDaily))).scalar() == ni
        assert (await db_session.execute(select(func.count()).select_from(MarginDaily))).scalar() == nm
        assert (await db_session.execute(select(func.count()).select_from(SblDaily))).scalar() == ns

    async def test_import_is_idempotent(self, db_session):
        await import_ohlcv(db_session, _load("twse_mi_index.json"), D)
        first = (await db_session.execute(select(func.count()).select_from(DailyPrice))).scalar()
        await import_ohlcv(db_session, _load("twse_mi_index.json"), D)  # 重跑
        second = (await db_session.execute(select(func.count()).select_from(DailyPrice))).scalar()
        assert first == second  # 不重複


class TestTpexParsers:
    """TPEx 上櫃 parser(fixtures 為 2026-09-04 實際回應切片,8069 元太實值)。"""

    def test_ohlcv(self):
        from app.importers import tpex

        stocks, prices = tpex.parse_ohlcv(_load("tpex_quotes.json"), D)
        assert all(s["market"] == "TPEx" for s in stocks)
        assert not any(p["symbol"] == "00411A" for p in prices)  # 非個股被過濾
        p = next(p for p in prices if p["symbol"] == "8069")
        assert p["close"] == 148.50 and p["open"] == 151.00
        assert p["volume"] == 5_360_833 and p["turnover"] == 802_799_712

    def test_institutional_grouping(self):
        from app.importers import tpex

        rows = tpex.parse_institutional(_load("tpex_inst.json"), D)
        r = next(r for r in rows if r["symbol"] == "8069")
        # 固定位置分組(經加總驗證):外資合計/投信/自營自行/避險
        assert r["foreign_net"] == -503_485
        assert r["trust_net"] == 121_000
        assert r["dealer_self_net"] == 32_929
        assert r["dealer_hedge_net"] == 22_897

    def test_margin(self):
        from app.importers import tpex

        rows = tpex.parse_margin(_load("tpex_margin.json"), D)
        r = next(r for r in rows if r["symbol"] == "8069")
        assert r["margin_balance"] == 11_015 and r["short_balance"] == 308

    async def test_import_to_db(self, db_session):
        from app.importers.service import (
            import_tpex_institutional,
            import_tpex_margin,
            import_tpex_ohlcv,
        )

        n = await import_tpex_ohlcv(db_session, _load("tpex_quotes.json"), D)
        ni = await import_tpex_institutional(db_session, _load("tpex_inst.json"), D)
        nm = await import_tpex_margin(db_session, _load("tpex_margin.json"), D)
        assert n == 3 and ni == 3 and nm == 3
        s = await db_session.get(Stock, "8069")
        assert s is not None and s.market == "TPEx"
