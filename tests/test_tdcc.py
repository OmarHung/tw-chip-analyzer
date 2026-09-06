"""TDCC parser + 匯入測試（用真實回應 fixture）。"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from sqlalchemy import func, select

from app.db.models.chips import TdccSummaryWeekly, TdccWeekly
from app.importers.service import import_tdcc
from app.importers.tdcc import parse_distribution

FIX = Path(__file__).parent / "fixtures" / "tdcc_1_5.json"


def _load() -> list[dict]:
    return json.loads(FIX.read_text(encoding="utf-8"))


class TestParse:
    def test_symbol_stripped_and_parsed(self):
        data_date, weekly, summary = parse_distribution(_load())
        assert isinstance(data_date, dt.date)
        syms = {r["symbol"] for r in summary}
        assert "2330" in syms  # 尾隨空白已 strip
        # 每檔 1-15 級距
        w2330 = [r for r in weekly if r["symbol"] == "2330"]
        assert len(w2330) == 15

    def test_summary_ratios_sum_to_100(self):
        _d, _w, summary = parse_distribution(_load())
        s = next(r for r in summary if r["symbol"] == "2330")
        total = (
            s["retail_ratio"] + s["medium_ratio"]
            + s["large_ratio"] + s["super_large_ratio"]
        )
        assert abs(total - 100.0) < 0.5  # 四個桶加總約 100%
        assert s["holder_count"] and s["holder_count"] > 0

    def test_tsmc_large_holder_dominant(self):
        # 台積電大戶+超大戶持股比應遠高於散戶
        _d, _w, summary = parse_distribution(_load())
        s = next(r for r in summary if r["symbol"] == "2330")
        assert s["large_ratio"] + s["super_large_ratio"] > s["retail_ratio"]

    def test_empty(self):
        assert parse_distribution([]) == (None, [], [])


class TestImport:
    async def test_import_tdcc(self, db_session):
        nw, ns = await import_tdcc(db_session, _load())
        assert ns >= 1 and nw == ns * 15
        assert (await db_session.execute(select(func.count()).select_from(TdccSummaryWeekly))).scalar() == ns
        assert (await db_session.execute(select(func.count()).select_from(TdccWeekly))).scalar() == nw
