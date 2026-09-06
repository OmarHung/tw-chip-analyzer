"""里程碑 E：API 整合測試（見 docs/05 §15）。"""
from __future__ import annotations

import datetime as dt

import httpx
import pytest_asyncio
from httpx import ASGITransport

from app.db.models.features import FeatureDaily
from app.db.models.market import Stock
from app.db.session import get_session
from app.main import app


async def _seed(session):
    session.add_all(
        [
            Stock(symbol="2330", name="台積電", market="TWSE", industry="半導體"),
            Stock(symbol="2317", name="鴻海", market="TWSE", industry="電子零組件"),
        ]
    )
    await session.flush()
    d = dt.date(2026, 9, 5)
    av = dt.datetime(2026, 9, 5, 15, 0)
    session.add_all(
        [
            FeatureDaily(
                symbol="2330", data_date=d, available_at=av,
                close=1000, atr14=15, ma20=980, vwap=995, recent_swing_low=950,
                turnover=50_000_000_000, close_vs_ma20_pct=0.02, close_vs_vwap_pct=0.005,
                foreign_5d_z=1.5, trust_5d_z=2.0, dealer_5d_z=0.5,
                margin_balance_change_z=-1.2, sbl_change_z=-0.3, short_balance_change_z=-0.2,
                large_holder_ratio_change_z=1.6, retail_holder_ratio_change_z=-1.3,
                holder_count_change_z=-0.9,
            ),
            FeatureDaily(
                symbol="2317", data_date=d, available_at=av,
                close=100, atr14=2, ma20=101, vwap=100, recent_swing_low=96,
                turnover=8_000_000_000, close_vs_ma20_pct=-0.01, close_vs_vwap_pct=0.0,
                foreign_5d_z=-1.5, trust_5d_z=-1.0, margin_balance_change_z=1.5,
                sbl_change_z=1.2, large_holder_ratio_change_z=-1.2,
                retail_holder_ratio_change_z=1.1,
            ),
        ]
    )
    await session.commit()


@pytest_asyncio.fixture
async def client(db_session):
    await _seed(db_session)

    # 讓 API 使用同一個測試 session
    async def _override():
        yield db_session

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


async def test_analysis_shape(client):
    r = await client.get("/api/stocks/2330/analysis")
    assert r.status_code == 200
    body = r.json()
    # 對應 docs/05 §15 的欄位
    for key in ("symbol", "price", "chip_score", "scores", "action", "risk", "reasons"):
        assert key in body
    assert body["symbol"] == "2330"
    assert set(body["scores"]) == {"intraday", "institutional", "holder", "market"}
    assert body["chip_score"] > 50  # 強籌碼
    assert body["reasons"]


async def test_analysis_404(client):
    r = await client.get("/api/stocks/9999/analysis")
    assert r.status_code == 404


async def test_scanner_sorted_and_filtered(client):
    r = await client.get("/api/scanner", params={"min_score": 0})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    scores = [row["chip_score"] for row in body["rows"]]
    assert scores == sorted(scores, reverse=True)  # 依分數排序
    assert body["rows"][0]["symbol"] == "2330"  # 強者在前


async def test_scanner_min_score_filter(client):
    r = await client.get("/api/scanner", params={"min_score": 55})
    body = r.json()
    assert all(row["chip_score"] >= 55 for row in body["rows"])
    assert "2317" not in [row["symbol"] for row in body["rows"]]


async def test_scanner_industry_filter(client):
    r = await client.get("/api/scanner", params={"industry": "半導體"})
    body = r.json()
    assert [row["symbol"] for row in body["rows"]] == ["2330"]


async def test_dashboard(client):
    r = await client.get("/api/dashboard")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert sum(body["action_counts"].values()) == 2
    assert body["top"][0]["symbol"] == "2330"  # 分數最高在前
    assert 0 <= body["avg_chip_score"] <= 100
