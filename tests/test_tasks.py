"""腳本按鈕：白名單任務、參數驗證、子行程執行、紀錄與取消。

安全前提：只能執行登錄表中的模組、只能帶宣告過的參數、絕不經 shell。
與 EOD / 回補共用 runner 單飛鎖，同時只跑一個工作。
"""
from __future__ import annotations

import asyncio

import httpx
import pytest
from httpx import ASGITransport

from app.core.config import get_settings
from app.db.models.jobs import JobRun
from app.jobs import runner, task_runner, tasks

ECHO = tasks.TaskSpec(
    id="echo",
    module="tests.helpers.echo_task",
    label="測試",
    category="maintenance",
    params=(
        tasks.ParamSpec("exit", "int", flag="--exit", min=0, max=5),
        tasks.ParamSpec("sleep", "float", flag="--sleep", min=0, max=10),
        tasks.ParamSpec("label", "choice", flag="--label", choices=("a", "b")),
    ),
)


class TestBuildArgv:
    def test_rebuild_signals_dates(self):
        spec = tasks.TASKS["rebuild_signals"]
        assert tasks.build_argv(spec, {"start": "2026-09-01", "end": "2026-09-08"}) == [
            "--start", "2026-09-01", "--end", "2026-09-08",
        ]

    def test_empty_values_are_omitted(self):
        assert tasks.build_argv(tasks.TASKS["rebuild_signals"], {"start": "", "end": None}) == []

    def test_invalid_date_rejected(self):
        with pytest.raises(ValueError):
            tasks.build_argv(tasks.TASKS["rebuild_signals"], {"start": "2026-13-01"})

    def test_unknown_param_rejected(self):
        with pytest.raises(ValueError):
            tasks.build_argv(tasks.TASKS["rebuild_signals"], {"cmd": "rm -rf /"})

    def test_positional_required(self):
        spec = tasks.TASKS["backfill_corporate_actions"]
        with pytest.raises(ValueError):
            tasks.build_argv(spec, {"start": "2026-09-01"})
        assert tasks.build_argv(spec, {"start": "2026-09-01", "end": "2026-09-08"}) == [
            "2026-09-01", "2026-09-08",
        ]

    def test_bool_flag_and_int_range(self):
        spec = tasks.TASKS["backfill_tdcc"]
        assert tasks.build_argv(spec, {"top": 50, "dry_run": True}) == ["--top", "50", "--dry-run"]
        assert tasks.build_argv(spec, {"dry_run": False}) == []
        with pytest.raises(ValueError):
            tasks.build_argv(spec, {"top": -1})

    def test_whitelisted_modules_exist(self):
        import importlib.util

        for spec in tasks.TASKS.values():
            assert importlib.util.find_spec(spec.module) is not None, spec.module


@pytest.fixture
def echo_registry(monkeypatch):
    monkeypatch.setitem(tasks.TASKS, "echo", ECHO)
    yield
    assert not runner.is_busy(), "任務結束後必須釋放單飛鎖"


async def _run(params, source="test"):
    run_id = await task_runner.start("echo", params, source=source)
    return await task_runner.wait(run_id)


async def test_success_captures_output(db_session, echo_registry):
    run = await _run({"label": "a"})
    assert run.status == "succeeded" and run.exit_code == 0
    assert "line 2 a" in run.output
    assert "argv=['--label', 'a']" in run.output
    assert run.data_version  # 啟動當下的設定版本（供重建提示比對）


async def test_nonzero_exit_is_failed(db_session, echo_registry):
    run = await _run({"exit": 3})
    assert run.status == "failed" and run.exit_code == 3


async def test_busy_lock_is_shared_with_backfill(db_session, echo_registry):
    assert runner.try_mark("single", "eod", "2026-09-08")
    try:
        with pytest.raises(task_runner.TaskBusy):
            await task_runner.start("echo", {}, source="test")
    finally:
        runner.finish()


async def test_cancel_running_task(db_session, echo_registry):
    run_id = await task_runner.start("echo", {"sleep": 5}, source="test")
    await asyncio.sleep(0.5)
    assert runner.is_busy()
    assert task_runner.cancel(run_id) is True
    run = await task_runner.wait(run_id)
    assert run.status == "cancelled"


async def test_stale_running_rows_marked_interrupted(db_session):
    db_session.add(JobRun(task_id="rebuild_signals", params={}, status="running", source="x"))
    await db_session.commit()
    assert await task_runner.mark_interrupted() == 1


@pytest.fixture
async def client(db_session, echo_registry):
    from app.db.session import get_session
    from app.main import app

    async def _override():
        yield db_session

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app, client=("127.0.0.1", 1))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def test_api_list_start_and_get_run(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "ops_api_key", "")
    listing = (await client.get("/api/ops/tasks")).json()
    assert any(t["id"] == "rebuild_signals" for t in listing["tasks"])

    r = await client.post("/api/ops/tasks/echo", json={"params": {"label": "b"}})
    assert r.status_code == 200
    run_id = r.json()["run_id"]
    await task_runner.wait(run_id)
    run = (await client.get(f"/api/ops/tasks/runs/{run_id}")).json()
    assert run["status"] == "succeeded" and "line 0 b" in run["output"]

    assert (await client.post("/api/ops/tasks/echo", json={"params": {"label": "zzz"}})).status_code == 422
    assert (await client.post("/api/ops/tasks/nope", json={"params": {}})).status_code == 404


async def test_api_start_requires_auth(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "ops_api_key", "k")
    assert (await client.post("/api/ops/tasks/echo", json={"params": {}})).status_code == 401
    assert (await client.get("/api/ops/tasks")).status_code == 200
