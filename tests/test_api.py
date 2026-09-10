"""里程碑 E：API 整合測試（見 docs/05 §15）。"""
from __future__ import annotations

import datetime as dt

import httpx
import pytest_asyncio
from httpx import ASGITransport

from app.connectors import yahoo
from app.db.models.features import FeatureDaily, SignalSnapshot
from app.db.models.market import DailyPrice, Stock
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


async def test_analysis_exposes_market(client, db_session):
    """個股資訊要能標示上市/上櫃（市場別只在 stock 主檔，需由 endpoint 補上）。"""
    r = await client.get("/api/stocks/2330/analysis")
    assert r.json()["market"] == "TWSE"
    # 上櫃股同樣要標對
    db_session.add(Stock(symbol="8069", name="元太", market="TPEx"))
    await db_session.flush()
    d = dt.date(2026, 9, 5)
    db_session.add(
        FeatureDaily(
            symbol="8069", data_date=d, available_at=dt.datetime(2026, 9, 5, 15, 0),
            close=148.5, atr14=3, ma20=150, vwap=149, recent_swing_low=140,
            turnover=800_000_000, close_vs_ma20_pct=-0.01, close_vs_vwap_pct=-0.003,
        )
    )
    await db_session.commit()
    r2 = await client.get("/api/stocks/8069/analysis")
    assert r2.status_code == 200 and r2.json()["market"] == "TPEx"


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


# --- /scores 時序 endpoint（讀 signal_snapshot） ---

async def _seed_snapshots(session):
    """為 2330 建 3 個交易日的分數時序（刻意亂序插入，驗證 API 升冪回傳）。"""
    dates = [dt.date(2026, 9, 5), dt.date(2026, 9, 3), dt.date(2026, 9, 4)]
    chips = {dt.date(2026, 9, 3): 60.0, dt.date(2026, 9, 4): 68.0, dt.date(2026, 9, 5): 72.0}
    session.add_all(
        [
            SignalSnapshot(
                symbol="2330", data_date=d,
                available_at=dt.datetime(d.year, d.month, d.day, 15, 0),
                chip_score=chips[d], intraday_score=None,
                institutional_score=70.0, holder_score=65.0, market_score=55.0,
                action="WATCH",
            )
            for d in dates
        ]
    )
    await session.commit()


async def test_scores_history(client, db_session):
    await _seed_snapshots(db_session)
    r = await client.get("/api/stocks/2330/scores")
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "2330"
    assert body["name"] == "台積電"
    assert body["count"] == 3
    ts = [p["t"] for p in body["points"]]
    assert ts == sorted(ts)  # 升冪
    first = body["points"][0]
    assert first["t"] == "2026-09-03"
    assert first["chip_score"] == 60.0
    # 四維分項齊全，intraday 缺值以 null 保留（不強制歸零）
    for key in ("intraday", "institutional", "holder", "market", "action"):
        assert key in first
    assert first["intraday"] is None
    assert first["institutional"] == 70.0


async def test_scores_history_empty(client):
    # 未寫入任何 snapshot 的標的 → count 0、points 空，不報錯
    r = await client.get("/api/stocks/2317/scores")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 0
    assert body["points"] == []


# --- /chart endpoint（自家日 K 優先，外部來源以 monkeypatch 隔離） ---

async def _seed_daily_prices(session, symbol="2330"):
    d = [dt.date(2026, 9, 3), dt.date(2026, 9, 4), dt.date(2026, 9, 5)]
    closes = [990, 1005, 1000]
    session.add_all(
        [
            DailyPrice(
                symbol=symbol, data_date=dd,
                available_at=dt.datetime(dd.year, dd.month, dd.day, 15, 0),
                open=c - 5, high=c + 8, low=c - 8, close=c, volume=10_000_000,
                turnover=c * 10_000_000,
            )
            for dd, c in zip(d, closes)
        ]
    )
    await session.commit()


async def test_chart_uses_local_daily(client, db_session, monkeypatch):
    await _seed_daily_prices(db_session)

    async def _no_intraday(symbol, market):  # 模擬外部盤中來源失敗
        raise RuntimeError("no network in test")

    async def _fail_daily(symbol, market, range_="1y"):  # 自家有資料，不該被呼叫
        raise AssertionError("有自家日 K 時不應 fallback Yahoo")

    monkeypatch.setattr(yahoo, "fetch_intraday", _no_intraday)
    monkeypatch.setattr(yahoo, "fetch_daily", _fail_daily)

    r = await client.get("/api/stocks/2330/chart")
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "2330"
    assert len(body["daily"]) == 3
    assert [b["t"] for b in body["daily"]] == sorted(b["t"] for b in body["daily"])  # 升冪
    assert body["daily"][-1]["c"] == 1000
    assert body["intraday"] == []  # 外部失敗被吞掉
    # prev_close fallback：intraday 失敗時取日 K 倒數第二根收盤
    assert body["prev_close"] == 1005


async def test_chart_fallback_to_yahoo_when_no_local(client, monkeypatch):
    # 2317 未 seed DailyPrice → 應 fallback 打 Yahoo 日 K
    fake_daily = [
        {"t": "2026-09-04", "o": 100, "h": 102, "l": 99, "c": 101, "v": 5_000_000},
        {"t": "2026-09-05", "o": 101, "h": 103, "l": 100, "c": 102, "v": 6_000_000},
    ]

    async def _fake_daily(symbol, market, range_="1y"):
        return fake_daily

    async def _no_intraday(symbol, market):
        raise RuntimeError("no network in test")

    monkeypatch.setattr(yahoo, "fetch_daily", _fake_daily)
    monkeypatch.setattr(yahoo, "fetch_intraday", _no_intraday)

    r = await client.get("/api/stocks/2317/chart")
    assert r.status_code == 200
    body = r.json()
    assert len(body["daily"]) == 2
    assert body["daily"][-1]["c"] == 102
    assert body["prev_close"] == 101  # 倒數第二根


async def test_features_endpoint_exposes_adjusted_values(client, db_session):
    """還原後的 MA/ATR 只存在 feature_daily；此端點讓它可被核對（6949 情境）。"""
    from app.db.models.market import CorporateAction

    d = dt.date(2026, 9, 5)
    av = dt.datetime(2026, 9, 5, 15, 0)
    await _seed_daily_prices(db_session)  # 2330 於 d 收 1000
    db_session.add(
        CorporateAction(
            symbol="2330", data_date=d, available_at=av, kind="面額",
            prev_close=20000, reference_price=1000, adj_factor=0.05, share_factor=20,
        )
    )
    await db_session.commit()

    r = await client.get("/api/stocks/2330/features", params={"date": "2026-09-05"})
    assert r.status_code == 200
    body = r.json()
    assert body["date"] == "2026-09-05" and body["name"] == "台積電"
    # feature 的 close/ma20 為還原值；raw_close 取自 daily_price 原始表
    assert body["ma20"] == 980 and body["atr14"] == 15
    assert body["close"] == 1000 and body["raw_close"] == 1000
    assert body["close_vs_ma20_pct"] == 0.02
    # 一併列出造成還原的公司行動，供人工核對因子
    assert body["actions"] == [
        {
            "date": "2026-09-05", "kind": "面額", "prev_close": 20000.0,
            "reference_price": 1000.0, "adj_factor": 0.05, "share_factor": 20.0,
        }
    ]


async def test_features_endpoint_defaults_to_latest_and_404s(client):
    r = await client.get("/api/stocks/2330/features")
    assert r.status_code == 200 and r.json()["date"] == "2026-09-05"
    # 指定沒有特徵的日期不往前找（核對用途要求精確日）
    r = await client.get("/api/stocks/2330/features", params={"date": "2026-09-04"})
    assert r.status_code == 404
    r = await client.get("/api/stocks/9999/features")
    assert r.status_code == 404


async def test_flows_backadjusts_corporate_action(db_session):
    """主力成本/股價線/累積買賣超跨拆股要還原：面額 1:20 後，成本應在現價尺度而非拆股前。"""
    import datetime as dt

    import httpx
    from httpx import ASGITransport

    from app.db.models.chips import InstitutionalDaily
    from app.db.models.market import CorporateAction
    from app.importers.base import availability_for

    sym = "9998"
    db_session.add(Stock(symbol=sym, name="拆股股", market="TWSE"))
    await db_session.flush()
    base = dt.date(2026, 9, 8)
    dates = [base - dt.timedelta(days=(24 - i)) for i in range(25)]
    split_date = dates[-1]  # 最後一日 1:20 面額變更
    for d in dates:
        px = 50.0 if d == split_date else 1000.0
        db_session.add(DailyPrice(
            symbol=sym, data_date=d, available_at=availability_for(d),
            open=px, high=px, low=px, close=px,
            volume=1_000_000, turnover=px * 1_000_000,
        ))
        net = 0 if d == split_date else 1_000_000  # 拆股前主力持續買超（股）
        db_session.add(InstitutionalDaily(
            symbol=sym, data_date=d, available_at=availability_for(d),
            foreign_net=net, trust_net=0, dealer_self_net=0, dealer_hedge_net=0,
        ))
    db_session.add(CorporateAction(
        symbol=sym, data_date=split_date, available_at=availability_for(split_date),
        kind="面額", prev_close=1000, reference_price=50, value=None,
        adj_factor=0.05, share_factor=20,
    ))
    await db_session.commit()

    async def _override():
        yield db_session

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get(f"/api/stocks/{sym}/flows", params={"days": 90})
    app.dependency_overrides.clear()

    assert r.status_code == 200
    j = r.json()
    cb = j["cost_basis"]
    # 還原後：主力成本在拆股後尺度（~50），不是拆股前的 ~1000；溢價不再是假的 -95%
    assert cb["latest_cost"] is not None and cb["latest_cost"] < 100
    assert cb["premium_pct"] > -0.2
    # 股價線也還原：最早一點 close ~50（不是原始 1000）
    assert j["points"][0]["close"] < 100
    # 張數還原到現股尺度：拆股前的日買超 1000 張 → ×20
    assert j["points"][0]["foreign"] == 20000


class TestCoverageGaps:
    """涵蓋缺口語意：只算「該源起始日之後」的空洞（見 repositories.ops._with_gaps）。"""

    def test_late_start_is_not_a_gap(self):
        """特徵/大盤前段沒資料是回看視窗造成，不是缺漏——算成缺口會誤導成該補。"""
        import datetime as dt

        from app.repositories.ops import _with_gaps

        cal = [dt.date(2026, 3, d) for d in (2, 3, 4, 5, 6)]
        # 這個源從第 3 天才開始（前 2 天是回看視窗），之後沒有空洞
        got = _with_gaps(cal[2:], cal)
        assert got["missing"] == 0
        assert got["missing_dates"] == []
        assert got["starts_late"] == 2

    def test_real_gap_is_reported_with_dates(self):
        import datetime as dt

        from app.repositories.ops import _with_gaps

        cal = [dt.date(2026, 3, d) for d in (2, 3, 4, 5, 6)]
        have = [cal[0], cal[1], cal[4]]          # 3/4、3/5 中間缺兩天
        got = _with_gaps(have, cal)
        assert got["missing"] == 2
        assert got["missing_dates"] == ["2026-03-04", "2026-03-05"]
        assert got["starts_late"] == 0

    def test_empty_source_has_no_gaps(self):
        """完全沒資料的源不該報一整個日曆的缺口（無從判斷起始日）。"""
        import datetime as dt

        from app.repositories.ops import _with_gaps

        cal = [dt.date(2026, 3, d) for d in (2, 3, 4)]
        got = _with_gaps([], cal)
        assert got["missing"] == 0 and got["starts_late"] == 0
