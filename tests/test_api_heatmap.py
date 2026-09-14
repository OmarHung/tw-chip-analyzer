"""熱力圖 API 測試（docs/05 §16）。

重點不在「有沒有回 200」，而在三件容易畫錯的事：
1. treemap 依成交值取前 N，且誠實回報涵蓋率
2. 產業矩陣用**中位數**、且成分股不足的產業不給格子（不是給 0）
3. 個股矩陣把「沒有這個成分」保持 NULL，不塞中性分數
"""
from __future__ import annotations

import datetime as dt

import httpx
import pytest_asyncio
from httpx import ASGITransport

from app.db.models.features import FeatureDaily, SignalSnapshot
from app.db.models.market import Stock
from app.db.session import get_session
from app.main import app

D1 = dt.date(2026, 9, 4)
D2 = dt.date(2026, 9, 5)
AV = dt.datetime(2026, 9, 5, 15, 0)

# 半導體 5 檔（達 min_symbols=5）、金融 2 檔（未達，應被排除）
SEMI = ["2330", "2454", "2303", "3034", "2408"]
FIN = ["2881", "2882"]
# 當日漲跌幅：半導體中位數 = 0.00（第 3 大者），金融即使中位數再高也不該出現
SEMI_CHANGES = [-0.02, -0.01, 0.00, 0.01, 0.03]
SEMI_TURNOVERS = [5e10, 2e10, 8e9, 3e9, 1e9]


async def _seed(session):
    session.add_all(
        [
            Stock(symbol=s, name=f"半導{s}", market="TWSE", industry="半導體",
                  website="https://www.tsmc.com" if s == "2330" else None)
            for s in SEMI
        ]
        + [Stock(symbol=s, name=f"金融{s}", market="TWSE", industry="金融保險") for s in FIN]
        # 無產業別（MOPS 查無）：treemap 仍應收錄，產業矩陣則無從歸類
        + [Stock(symbol="9999", name="未分類", market="TPEx", industry=None)]
    )
    await session.flush()

    def feature(symbol: str, date: dt.date, change: float, turnover: float) -> FeatureDaily:
        return FeatureDaily(
            symbol=symbol, data_date=date, available_at=AV,
            close=100, change_pct=change, atr14=2, ma20=99, vwap=100,
            recent_swing_low=95, turnover=turnover,
            close_vs_ma20_pct=0.01, close_vs_vwap_pct=0.0,
            foreign_5d_z=1.0, trust_5d_z=0.5, margin_balance_change_z=-0.5,
            large_holder_ratio_change_z=0.8, retail_holder_ratio_change_z=-0.6,
        )

    rows = []
    for date in (D1, D2):
        for sym, chg, to in zip(SEMI, SEMI_CHANGES, SEMI_TURNOVERS):
            rows.append(feature(sym, date, chg, to))
        for sym in FIN:
            rows.append(feature(sym, date, 0.05, 4e9))
        rows.append(feature("9999", date, -0.03, 5e8))
    session.add_all(rows)

    # 已落地分數：只給 D2，且 2330 沒有 intraday/holder（模擬當日無逐筆、無 TDCC）
    session.add_all([
        SignalSnapshot(
            symbol="2330", data_date=D2, available_at=AV, chip_score=88.0,
            intraday_score=None, institutional_score=70.0, holder_score=None,
            market_score=60.0, action="BUY",
        ),
        SignalSnapshot(
            symbol="2330", data_date=D1, available_at=AV, chip_score=60.0,
            intraday_score=55.0, institutional_score=50.0, holder_score=45.0,
            market_score=60.0, action="WATCH",
        ),
        SignalSnapshot(
            symbol="2454", data_date=D2, available_at=AV, chip_score=40.0,
            institutional_score=30.0, action="HOLD",
        ),
    ])
    await session.commit()


@pytest_asyncio.fixture
async def client(db_session):
    await _seed(db_session)

    async def _override():
        yield db_session

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


# ------------------------------------------------------------------ treemap

async def test_market_heatmap_sorted_by_turnover_and_reports_coverage(client):
    r = await client.get("/api/heatmap/market")
    assert r.status_code == 200
    data = r.json()
    turnovers = [row["turnover"] for row in data["rows"]]
    assert turnovers == sorted(turnovers, reverse=True)   # 面積大的先畫
    assert data["as_of"] == str(D2)
    assert 0 < data["covered_turnover"] <= 1.0
    assert data["count"] == data["total"]                  # 未截斷時涵蓋全部
    assert data["covered_turnover"] == 1.0


async def test_market_heatmap_limit_truncates_and_lowers_coverage(client):
    r = await client.get("/api/heatmap/market?limit=2")
    data = r.json()
    assert data["count"] == 2 and data["total"] > 2
    # 只畫最大的兩檔 → 涵蓋率必須誠實降低，UI 才能標示「涵蓋 xx% 成交值」
    assert data["covered_turnover"] < 1.0
    assert [row["symbol"] for row in data["rows"]] == ["2330", "2454"]


async def test_market_heatmap_includes_industry_and_unclassified(client):
    r = await client.get("/api/heatmap/market?limit=100")
    rows = {row["symbol"]: row for row in r.json()["rows"]}
    assert rows["2330"]["industry"] == "半導體"
    assert rows["9999"]["industry"] is None   # 無產業別不代表要被丟掉


async def test_market_heatmap_includes_name_and_website_for_logo(client):
    r = await client.get("/api/heatmap/market?limit=100")
    rows = {row["symbol"]: row for row in r.json()["rows"]}
    assert rows["2330"]["name"] == "半導2330"
    assert rows["2330"]["website"] == "https://www.tsmc.com"
    assert rows["2454"]["website"] is None    # 無網址 → 前端退回代號徽章


async def test_market_heatmap_min_turnover_filters(client):
    r = await client.get("/api/heatmap/market?min_turnover=1e10")
    assert [row["symbol"] for row in r.json()["rows"]] == ["2330", "2454"]


# ------------------------------------------------------------ 產業 × 日期

async def test_industry_heatmap_uses_median_not_mean(client):
    r = await client.get("/api/heatmap/industry")
    assert r.status_code == 200
    data = r.json()
    semi = next(row for row in data["industries"] if row["industry"] == "半導體")
    cell = next(c for c in semi["cells"] if c["date"] == str(D2))
    # 平均是 +0.002，中位數是 0.0：極端值不得帶著整個產業跑
    assert cell["ret"] == 0.0
    assert cell["n"] == 5


async def test_industry_heatmap_excludes_industries_below_min_symbols(client):
    r = await client.get("/api/heatmap/industry")
    names = [row["industry"] for row in r.json()["industries"]]
    assert "半導體" in names
    assert "金融保險" not in names   # 只有 2 檔 < min_symbols=5，寧可留白


async def test_industry_heatmap_min_symbols_override_lets_small_industry_in(client):
    r = await client.get("/api/heatmap/industry?min_symbols=2")
    names = [row["industry"] for row in r.json()["industries"]]
    assert "金融保險" in names


async def test_industry_heatmap_dates_are_trading_days_oldest_first(client):
    data = (await client.get("/api/heatmap/industry")).json()
    assert data["dates"] == [str(D1), str(D2)]   # 由舊到新，且只含有資料的日子
    assert data["as_of"] == str(D2)


async def test_industry_heatmap_days_limits_window(client):
    data = (await client.get("/api/heatmap/industry?days=2")).json()
    assert data["dates"] == [str(D1), str(D2)]


async def test_industry_heatmap_carries_score_and_inst_for_color_switching(client):
    """三個 metric 一次給齊：前端換顏色維度不該重打 API。"""
    data = (await client.get("/api/heatmap/industry")).json()
    semi = next(row for row in data["industries"] if row["industry"] == "半導體")
    d2 = next(c for c in semi["cells"] if c["date"] == str(D2))
    # D2 有兩筆 snapshot（88 / 40）→ 中位數 64；D1 只有 2330（60）
    assert d2["score"] == 64.0
    assert d2["inst"] == 50.0
    d1 = next(c for c in semi["cells"] if c["date"] == str(D1))
    assert d1["score"] == 60.0


# ---------------------------------------------------------- 個股分項矩陣

async def test_stock_heatmap_oldest_first_and_keeps_null_components(client):
    data = (await client.get("/api/heatmap/stock/2330")).json()
    assert [c["date"] for c in data["cells"]] == [str(D1), str(D2)]
    latest = data["cells"][-1]
    # 當日無逐筆／無 TDCC：必須是 null，不能填 50（UI 要畫成「無資料」而非「中性」）
    assert latest["intraday"] is None and latest["holder"] is None
    assert latest["institutional"] == 70.0
    assert latest["action"] == "BUY"


async def test_stock_heatmap_days_takes_most_recent(client):
    data = (await client.get("/api/heatmap/stock/2330?days=2")).json()
    assert len(data["cells"]) == 2
    assert data["cells"][-1]["date"] == str(D2)   # 截斷時保留最新


async def test_stock_heatmap_unknown_symbol_is_empty_not_error(client):
    r = await client.get("/api/heatmap/stock/0000")
    assert r.status_code == 200 and r.json()["cells"] == []


# ------------------------------------------------------------------ 空資料

async def test_heatmaps_on_empty_db_return_safe_payload(db_session):
    async def _override():
        yield db_session

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        market = (await c.get("/api/heatmap/market")).json()
        industry = (await c.get("/api/heatmap/industry")).json()
    app.dependency_overrides.clear()

    assert market["as_of"] is None and market["rows"] == []
    assert market["covered_turnover"] == 0.0   # 不可 ZeroDivisionError
    assert industry["dates"] == [] and industry["industries"] == []
