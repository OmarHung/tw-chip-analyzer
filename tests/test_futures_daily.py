"""台指期（TAIFEX）parser / 匯入 / 主力月份選取 / 概覽 API 漲跌幅。

真實 CSV 切片（2026/09/16~17 的 TX），不打外部 API。
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import httpx
from httpx import ASGITransport

from app.db.models.features import FeatureDaily
from app.db.models.market import FuturesDaily, MarketDaily, MarketIndex, Stock
from app.importers.base import availability_for
from app.importers.service import import_futures
from app.importers.taifex import parse_futures_daily
from app.repositories.market import load_futures_front_month, load_prev_taiex_close

FIX = Path(__file__).parent / "fixtures"
CSV = (FIX / "taifex_tx_daily.csv").read_text(encoding="utf-8")

D = dt.date(2026, 9, 17)
PREV = dt.date(2026, 9, 16)


# ---------------- parser ----------------

def test_parse_keeps_regular_session_single_contracts_only():
    rows = parse_futures_daily(CSV)
    assert rows, "fixture 應有資料"
    # 夜盤半截資料不可混入；價差契約報的是價差不是點位
    assert all("/" not in r["contract_month"] for r in rows)
    assert {r["contract"] for r in rows} == {"TX"}
    d16 = [r for r in rows if r["data_date"] == PREV]
    assert [r["contract_month"] for r in d16] == ["202609", "202610", "202611",
                                                  "202612", "202703", "202706"]


def test_parse_fields_and_pct_scale():
    front = next(
        r for r in parse_futures_daily(CSV)
        if r["data_date"] == PREV and r["contract_month"] == "202610"
    )
    assert front["close"] == 46078.0
    assert front["change"] == 351.0
    assert front["change_pct"] == 0.0077  # '0.77%' → 小數
    assert front["volume"] == 36544
    assert front["open_interest"] == 95896
    assert front["available_at"] == availability_for(PREV)


# ---------------- 匯入 + 主力月份 ----------------

async def test_import_is_idempotent(db_session):
    n1 = await import_futures(db_session, CSV)
    n2 = await import_futures(db_session, CSV)
    assert n1 == n2 > 0
    rows = (await db_session.execute(FuturesDaily.__table__.select())).all()
    assert len(rows) == n1


async def test_front_month_is_the_most_traded_not_the_nearest(db_session):
    """結算日當天近月量已萎縮，取「成交量最大」而非「到期月份最小」。"""
    await import_futures(db_session, CSV)
    fut = await load_futures_front_month(db_session, PREV)
    assert fut is not None
    assert fut.contract_month == "202610"  # 近月 202609 量 28491 < 202610 的 36544
    assert float(fut.close) == 46078.0


async def test_front_month_does_not_fall_back_to_previous_day(db_session):
    await import_futures(db_session, CSV)
    assert await load_futures_front_month(db_session, dt.date(2026, 9, 18)) is None


# ---------------- 概覽 API ----------------

async def _seed_dashboard(session) -> None:
    session.add(Stock(symbol="2330", name="台積電", market="TWSE"))
    await session.flush()
    session.add(
        FeatureDaily(
            symbol="2330", data_date=D, available_at=availability_for(D),
            close=100, atr14=2, ma20=100, vwap=100, recent_swing_low=96,
            turnover=5e8, close_vs_ma20_pct=0.0, close_vs_vwap_pct=0.0,
            resistance_high=130, is_limit_locked=False,
            foreign_5d_z=1.0, trust_5d_z=1.0, dealer_5d_z=1.0,
            margin_balance_change_z=-1.0, short_balance_change_z=-1.0,
        )
    )
    session.add(
        MarketDaily(data_date=D, available_at=availability_for(D),
                    taiex_close=46288, market_trend_score=0.6)
    )
    session.add(
        MarketIndex(data_date=PREV, available_at=availability_for(PREV),
                    taiex_close=46000)
    )
    await import_futures(session, CSV)
    await session.commit()


async def _dashboard(session) -> dict:
    """打 /api/dashboard，session 覆寫成測試 session（同 test_market_availability）。"""
    from app.db.session import get_session
    from app.main import app

    async def _override():
        yield session

    app.dependency_overrides[get_session] = _override
    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.get("/api/dashboard")
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 200
    return r.json()


async def test_prev_taiex_close_is_strictly_before_target(db_session):
    await _seed_dashboard(db_session)
    assert await load_prev_taiex_close(db_session, D) == 46000.0
    assert await load_prev_taiex_close(db_session, PREV) is None


async def test_dashboard_reports_taiex_and_futures_change(db_session):
    await _seed_dashboard(db_session)
    m = (await _dashboard(db_session))["market"]
    assert m["taiex_close"] == 46288.0
    assert m["taiex_change"] == 288.0
    assert round(m["taiex_change_pct"], 6) == round(288 / 46000, 6)
    assert m["futures"]["contract_month"] == "202610"
    assert m["futures"]["close"] == 46445.0
    assert m["futures"]["change"] == 385.0
    assert m["futures"]["change_pct"] == 0.0084


async def test_dashboard_without_futures_or_prev_close(db_session):
    """缺前收/缺當日期貨 → 該欄位 None，不沿用前一日、不猜。"""
    session = db_session
    session.add(Stock(symbol="2330", name="台積電", market="TWSE"))
    await session.flush()
    session.add(
        FeatureDaily(
            symbol="2330", data_date=D, available_at=availability_for(D),
            close=100, atr14=2, ma20=100, vwap=100, recent_swing_low=96,
            turnover=5e8, close_vs_ma20_pct=0.0, close_vs_vwap_pct=0.0,
            resistance_high=130, is_limit_locked=False, foreign_5d_z=1.0,
        )
    )
    session.add(
        MarketDaily(data_date=D, available_at=availability_for(D),
                    taiex_close=46288, market_trend_score=0.6)
    )
    await session.commit()
    m = (await _dashboard(session))["market"]
    assert m["taiex_change"] is None
    assert m["taiex_change_pct"] is None
    assert m["futures"] is None
