"""個股商標代理 `/api/stocks/{symbol}/logo`。

為何要代理而不讓瀏覽器直連 favicon 服務：Google 對查無 favicon 的網域回 **404 + 16px
預設地球圖**，瀏覽器照樣觸發 onLoad，前端無法分辨「台積電真的只有 16px favicon」與
「查無」。後端看得到上游狀態碼，查無一律回 404，前端才能確實退回首字徽章。
"""
from __future__ import annotations

import httpx
import pytest_asyncio
from httpx import ASGITransport

from app.api import logos
from app.db.models.market import Stock
from app.db.session import get_session
from app.main import app

PNG = b"\x89PNG\r\n\x1a\nfake"


@pytest_asyncio.fixture
async def client(db_session, monkeypatch):
    db_session.add_all([
        Stock(symbol="2330", name="台積電", market="TWSE", website="https://www.tsmc.com"),
        Stock(symbol="2408", name="南亞科", market="TWSE", website="https://www.nanya.com"),
        Stock(symbol="0050", name="元大台灣50", market="TWSE", website=None),
    ])
    await db_session.commit()

    calls: list[str] = []

    async def fake_fetch(host: str, size: int, timeout: float):
        calls.append(host)
        return (PNG, "image/png") if host == "www.tsmc.com" else None

    monkeypatch.setattr(logos, "fetch_favicon", fake_fetch)
    logos.clear_cache()

    async def _override():
        yield db_session

    app.dependency_overrides[get_session] = _override
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.calls = calls  # type: ignore[attr-defined]
        yield c
    app.dependency_overrides.clear()
    logos.clear_cache()


async def test_logo_returns_image_with_cache_header(client):
    r = await client.get("/api/stocks/2330/logo")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content == PNG
    assert "max-age" in r.headers["cache-control"]


async def test_logo_404_when_upstream_has_no_favicon(client):
    r = await client.get("/api/stocks/2408/logo")
    assert r.status_code == 404


async def test_logo_404_when_stock_has_no_website_or_unknown(client):
    assert (await client.get("/api/stocks/0050/logo")).status_code == 404
    assert (await client.get("/api/stocks/9999/logo")).status_code == 404
    assert client.calls == []   # 沒網址就不打上游


async def test_logo_caches_hits_and_misses(client):
    for _ in range(3):
        await client.get("/api/stocks/2330/logo")
        await client.get("/api/stocks/2408/logo")
    assert client.calls == ["www.tsmc.com", "www.nanya.com"]


async def test_logo_upstream_error_is_404_and_not_cached(client, monkeypatch):
    async def boom(host: str, size: int, timeout: float):
        raise httpx.ConnectTimeout("timeout")

    monkeypatch.setattr(logos, "fetch_favicon", boom)
    assert (await client.get("/api/stocks/2330/logo")).status_code == 404

    async def ok(host: str, size: int, timeout: float):
        return PNG, "image/png"

    monkeypatch.setattr(logos, "fetch_favicon", ok)
    # 暫時性網路錯誤不可被當成「查無」快取住
    assert (await client.get("/api/stocks/2330/logo")).status_code == 200
