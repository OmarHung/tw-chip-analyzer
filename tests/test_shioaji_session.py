"""Shioaji session 失效後必須丟棄,否則永遠用著壞掉的連線。

實際事故(2026-09-12):token 過期後 _api 仍快取著失效 session,
usage() 每次都失敗(401 / SessionNotEstablished)且永不重新登入,直到容器重啟。
"""
from __future__ import annotations

from app.connectors import shioaji_market as sm


class _DeadApi:
    def usage(self):
        raise RuntimeError("StatusCode: 401, Detail: Token is expired")


def test_failed_usage_drops_cached_session(monkeypatch):
    monkeypatch.setattr(sm, "_api", _DeadApi())

    assert sm.usage_sync() is None
    assert sm._api is None, "session 失效後未丟棄,下次不會重新登入"


def test_successful_usage_keeps_session(monkeypatch):
    class _Usage:
        bytes = 10
        limit_bytes = 100

    class _LiveApi:
        def usage(self):
            return _Usage()

    live = _LiveApi()
    monkeypatch.setattr(sm, "_api", live)

    out = sm.usage_sync()
    assert out == {"bytes": 10, "limit_bytes": 100, "used_pct": 10.0}
    assert sm._api is live, "正常時不該丟棄 session"
