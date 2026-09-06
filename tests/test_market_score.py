"""TAIEX index parser + 大盤 regime 測試。"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from app.db.models.market import DailyPrice, MarketDaily, MarketIndex, Stock
from app.importers.base import availability_for, parse_roc_date
from app.importers.twse import parse_index
from app.services.market_score import build_market_daily

FIX = Path(__file__).parent / "fixtures" / "twse_fmtqik.json"


class TestIndexParser:
    def test_roc_date(self):
        assert parse_roc_date("115/09/04") == dt.date(2026, 9, 4)
        assert parse_roc_date("bad") is None

    def test_parse_index(self):
        rows = parse_index(json.loads(FIX.read_text(encoding="utf-8")))
        assert rows
        assert all(r["taiex_close"] is not None for r in rows)
        assert rows[-1]["data_date"].year == 2026


class TestMarketDaily:
    async def test_uptrend_positive_score(self, db_session):
        # 造一段上升 TAIEX（收在 MA 之上、正斜率）+ 多數股上漲
        target = dt.date(2026, 6, 30)
        for i in range(65):
            d = target - dt.timedelta(days=64 - i)
            db_session.add(
                MarketIndex(
                    data_date=d, available_at=availability_for(d),
                    taiex_close=15000 + i * 30,
                )
            )
        # 兩檔股票，今日都上漲（breadth 正）。prev 的指數已由上面迴圈建立。
        prev = target - dt.timedelta(days=1)
        for sym in ("AAA", "BBB"):
            db_session.add(Stock(symbol=sym, name=sym, market="TWSE"))
        await db_session.flush()
        for sym, base in (("AAA", 100), ("BBB", 50)):
            for d, c in ((prev, base), (target, base * 1.03)):
                db_session.add(
                    DailyPrice(symbol=sym, data_date=d, available_at=availability_for(d),
                               open=c, high=c, low=c, close=c, volume=1000, turnover=c * 1000)
                )
        await db_session.commit()

        ok = await build_market_daily(db_session, target)
        assert ok
        md = await db_session.get(MarketDaily, 1)
        assert md.market_trend_score > 0
        assert md.taiex_ma20 is not None and md.taiex_ma60 is not None
        assert md.advancers == 2 and md.decliners == 0

    async def test_no_index_returns_false(self, db_session):
        assert await build_market_daily(db_session, dt.date(2026, 6, 30)) is False
