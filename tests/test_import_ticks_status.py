"""docs/09 第 2 批：Shioaji 空資料與失敗必須可區分（BUG-09）。"""
from __future__ import annotations

import datetime as dt

import pytest

from app.db.models.market import DailyPrice, Stock
from app.importers.base import availability_for
from app.jobs import import_ticks
from app.services import ticks as ticks_svc

TARGET = dt.date(2026, 9, 8)
TICK = {
    "ts": dt.datetime(2026, 9, 8, 9, 0, 1), "price": 100.0, "volume": 1,
    "bid": 99.5, "ask": 100.0, "side": 1,
}


async def _seed(session, symbols):
    for sym in symbols:
        session.add(Stock(symbol=sym, name=sym, market="TWSE"))
    await session.flush()
    for i, sym in enumerate(symbols):
        session.add(
            DailyPrice(
                symbol=sym, data_date=TARGET, available_at=availability_for(TARGET),
                open=100, high=100, low=100, close=100, volume=1_000_000,
                turnover=1e10 - i,  # 依序排名
            )
        )
    await session.commit()


def _raise(exc):
    raise exc


@pytest.fixture
def quiet(monkeypatch):
    """關掉配額查詢與節流；記錄 session 重置次數。"""
    monkeypatch.setattr(import_ticks, "usage_sync", lambda: None)
    cfg = import_ticks.get_thresholds().intraday_batch
    monkeypatch.setitem(cfg, "throttle_ms", 0)
    resets: list[int] = []
    monkeypatch.setattr(ticks_svc, "reset_api", lambda: resets.append(1))
    return resets


async def test_fetch_ticks_statuses(db_session, monkeypatch, quiet):
    await _seed(db_session, ["OK", "EMPTY", "BAD"])
    behaviour = {"OK": [TICK], "EMPTY": []}
    monkeypatch.setattr(
        ticks_svc, "fetch_ticks_sync",
        lambda s, d: behaviour[s] if s in behaviour
        else _raise(RuntimeError("401 Token is expired")),
    )
    ok = await ticks_svc.fetch_ticks(db_session, "OK", TARGET)
    assert ok.status == "fetched" and len(ok.ticks) == 1
    assert (await ticks_svc.fetch_ticks(db_session, "OK", TARGET)).status == "cached"
    assert (await ticks_svc.fetch_ticks(db_session, "EMPTY", TARGET)).status == "empty"
    bad = await ticks_svc.fetch_ticks(db_session, "BAD", TARGET)
    assert bad.status == "failed" and "Token" in bad.error
    assert quiet  # 失敗時丟棄 session，下次重新登入


async def test_batch_counts_empty_and_failed_separately(db_session, monkeypatch, quiet):
    await _seed(db_session, ["S1", "S2", "S3"])
    behaviour = {"S1": [TICK], "S2": []}
    monkeypatch.setattr(
        ticks_svc, "fetch_ticks_sync",
        lambda s, d: behaviour[s] if s in behaviour else _raise(ConnectionError("down")),
    )
    res = await import_ticks.run(TARGET)
    assert res["fetched"] == 1
    assert res["empty"] == 1
    assert res["failed"] == 1
    assert res["skipped"] == 0 and res["stopped"] is False


async def test_batch_stops_after_consecutive_failures(db_session, monkeypatch, quiet):
    await _seed(db_session, [f"F{i}" for i in range(8)])
    monkeypatch.setattr(
        ticks_svc, "fetch_ticks_sync", lambda s, d: _raise(ConnectionError("down"))
    )
    monkeypatch.setitem(
        import_ticks.get_thresholds().intraday_batch, "max_consecutive_failures", 3
    )
    res = await import_ticks.run(TARGET)
    assert res["stopped"] is True
    assert res["failed"] == 3
    assert res["skipped"] == 5
    assert "連續" in res["stop_reason"]


async def test_ticks_endpoint_returns_502_on_fetch_failure(db_session, monkeypatch, quiet):
    import httpx
    from httpx import ASGITransport

    from app.db.session import get_session
    from app.main import app

    await _seed(db_session, ["BAD"])
    monkeypatch.setattr(
        ticks_svc, "fetch_ticks_sync", lambda s, d: _raise(ConnectionError("down"))
    )

    async def _override():
        yield db_session

    app.dependency_overrides[get_session] = _override
    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            for path in ("ticks", "orderflow"):
                r = await c.get(f"/api/stocks/BAD/{path}", params={"date": str(TARGET)})
                assert r.status_code == 502, path
    finally:
        app.dependency_overrides.clear()
