"""docs/09 第 4 批：ops 寫入型端點認證（RISK-01）。

- 設了 OPS_API_KEY：必須帶正確 X-Ops-Key（常數時間比對）。
- 沒設：只接受本機直連（loopback 且無 X-Forwarded-For）；經反向代理一律拒絕——
  nginx / tailscale serve 轉發時 client 也是 127.0.0.1，不能只看來源 IP。
- 每次觸發都寫稽核 log（來源、認證方式、參數），金鑰本身不入 log。
"""
from __future__ import annotations

import logging

import httpx
import pytest
from httpx import ASGITransport

from app.api import ops
from app.core.config import get_settings
from app.main import app

BODY = {"kind": "single", "date": "2026-09-08", "mode": "eod"}


async def _no_quota():
    return ops.Quota(available=False)


@pytest.fixture
def fake_runner(monkeypatch):
    started: list[tuple] = []
    monkeypatch.setattr(ops.runner, "is_busy", lambda: False)
    monkeypatch.setattr(ops.runner, "start_single", lambda d, m: started.append((d, m)) or True)
    monkeypatch.setattr(ops, "_get_quota", _no_quota)
    return started


@pytest.fixture
def key(monkeypatch):
    def _set(value: str):
        monkeypatch.setattr(get_settings(), "ops_api_key", value)
    return _set


async def _post(headers=None, client=("127.0.0.1", 50000)):
    transport = ASGITransport(app=app, client=client)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        return await c.post("/api/ops/backfill", json=BODY, headers=headers or {})


async def test_no_key_allows_direct_loopback(fake_runner, key):
    key("")
    r = await _post()
    assert r.status_code == 200 and fake_runner


async def test_no_key_rejects_proxied_request(fake_runner, key):
    key("")
    r = await _post(headers={"X-Forwarded-For": "100.64.0.7"})
    assert r.status_code == 403 and not fake_runner


async def test_no_key_rejects_non_loopback_client(fake_runner, key):
    key("")
    r = await _post(client=("100.64.0.7", 50000))
    assert r.status_code == 403 and not fake_runner


async def test_key_required_when_configured(fake_runner, key):
    key("s3cret-key")
    assert (await _post()).status_code == 401
    assert (await _post(headers={"X-Ops-Key": "wrong"})).status_code == 401
    assert not fake_runner


async def test_correct_key_allows_even_behind_proxy(fake_runner, key):
    key("s3cret-key")
    r = await _post(headers={"X-Ops-Key": "s3cret-key", "X-Forwarded-For": "100.64.0.7"})
    assert r.status_code == 200 and fake_runner


async def test_backfill_writes_audit_log(fake_runner, key, caplog):
    key("s3cret-key")
    with caplog.at_level(logging.INFO):
        await _post(headers={"X-Ops-Key": "s3cret-key", "X-Forwarded-For": "100.64.0.7"})
        await _post(headers={"X-Ops-Key": "wrong"})
    audit = [r.getMessage() for r in caplog.records if "ops 稽核" in r.getMessage()]
    assert any("允許" in m and "100.64.0.7" in m and "single" in m for m in audit)
    assert any("拒絕" in m for m in audit)
    assert not any("s3cret-key" in m for m in audit)  # 金鑰不可寫進 log


async def test_status_stays_readable_without_key(key, monkeypatch):
    key("s3cret-key")
    monkeypatch.setattr(ops, "_get_quota", _no_quota)
    transport = ASGITransport(app=app, client=("100.64.0.7", 1))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.get("/api/ops/status")).status_code == 200
