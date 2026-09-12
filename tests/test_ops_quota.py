"""配額查詢的隔離性：Shioaji 卡死不得拖垮整個 API。

實際事故(2026-09-12)：Shioaji token 過期後 usage() 卡在 session 重建，
ops._get_quota 用 asyncio.to_thread + wait_for —— 逾時取消不了 thread，
失敗又不快取，於是前端每次輪詢就洩漏一條永久卡死的 thread，
填滿全域 default executor 後，所有同步路由(含與 Shioaji 無關的 /api/dashboard)
都排不進 executor，uvicorn 接了連線卻不回應。
"""
from __future__ import annotations

import asyncio
import threading

import pytest

from app.api import ops


@pytest.fixture(autouse=True)
def _reset_quota_state():
    ops._quota_cache = None
    ops._quota_at = 0.0
    ops._quota_failed_at = 0.0
    yield
    ops._quota_cache = None
    ops._quota_at = 0.0
    ops._quota_failed_at = 0.0


@pytest.mark.asyncio
async def test_hung_usage_does_not_leak_a_thread_per_poll(monkeypatch):
    """usage() 永久卡死時，反覆輪詢只能佔用一條 thread(且非 default executor)。"""
    release = threading.Event()
    calls = 0

    def hung_usage():
        nonlocal calls
        calls += 1
        release.wait()  # 模擬 Shioaji session 重建永不返回
        return None

    monkeypatch.setattr("app.connectors.shioaji_market.usage_sync", hung_usage)
    monkeypatch.setattr(ops, "_QUOTA_TIMEOUT", 0.1)

    try:
        for _ in range(5):
            q = await ops._get_quota()
            assert q.available is False
        # 單飛 + 負快取：卡死的呼叫不得每次輪詢都重開一條
        assert calls == 1, f"每次輪詢都開新 thread，洩漏 {calls} 條"
    finally:
        release.set()


@pytest.mark.asyncio
async def test_hung_usage_leaves_default_executor_usable(monkeypatch):
    """配額卡死後，其他同步工作(FastAPI 的 def 路由走 default executor)仍須可執行。"""
    release = threading.Event()

    def hung_usage():
        release.wait()
        return None

    monkeypatch.setattr("app.connectors.shioaji_market.usage_sync", hung_usage)
    monkeypatch.setattr(ops, "_QUOTA_TIMEOUT", 0.1)

    try:
        for _ in range(40):  # 遠超 default executor 的 min(32, CPU+4)
            await ops._get_quota()
        # default executor 仍須能接新工作
        done = await asyncio.wait_for(asyncio.to_thread(lambda: "ok"), 5.0)
        assert done == "ok"
    finally:
        release.set()


@pytest.mark.asyncio
async def test_failure_is_negatively_cached(monkeypatch):
    """usage() 快速失敗(回 None)也不得每次輪詢都重打 Shioaji。"""
    calls = 0

    def failing_usage():
        nonlocal calls
        calls += 1
        return None

    monkeypatch.setattr("app.connectors.shioaji_market.usage_sync", failing_usage)

    for _ in range(5):
        q = await ops._get_quota()
        assert q.available is False
    assert calls == 1, f"失敗未負快取，重打 {calls} 次"
