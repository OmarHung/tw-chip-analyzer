"""白名單腳本的子行程執行器（系統頁「工作」按鈕）。

- **子行程**而非 in-process：重建/回補是長時間 pandas 運算，放在 API 的 event loop 會讓
  整個網站無回應；子行程另以 nice 降優先權，1 vCPU 主機上 API 仍能搶到 CPU。
- **與 EOD / 回補共用 runner 單飛鎖**：同時只跑一個工作（避免資料/配額打架）。
  注意：工作進行中到了排程時間，EOD 會被跳過（沿用既有行為），UI 需提示。
- 輸出逐行收集（即時可查），結束時連同結束碼寫入 job_run；超過上限保留尾端。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import signal
import sys
from zoneinfo import ZoneInfo

from sqlalchemy import select, update

from app.core.config import PROJECT_ROOT, get_thresholds
from app.core.logging import get_logger
from app.core.threshold_registry import data_version
from app.db.models.jobs import JobRun
from app.db.session import get_sessionmaker
from app.jobs import runner, tasks

logger = get_logger("jobs.task_runner")
_TZ = ZoneInfo("Asia/Taipei")


class TaskBusy(Exception):
    """已有 EOD / 回補 / 工作進行中。"""


_running: dict[int, asyncio.Task] = {}
_procs: dict[int, asyncio.subprocess.Process] = {}
_buffers: dict[int, list[str]] = {}
_cancelled: set[int] = set()


def _cfg() -> dict:
    return get_thresholds().get("jobs", default={}) or {}


def _trim(text: str) -> str:
    limit = int(_cfg().get("output_max_chars", 200_000))
    if len(text) <= limit:
        return text
    return "…（前略）\n" + text[-limit:]


def _lower_priority() -> None:  # 在子行程 exec 前執行
    try:
        os.nice(int(_cfg().get("nice", 10)))
    except OSError:
        pass


async def start(task_id: str, params: dict, *, source: str) -> int:
    """驗證 → 搶單飛鎖 → 寫 job_run → 背景啟動子行程。回傳 run id。

    KeyError（未知任務）、ValueError（參數不合法）、TaskBusy（忙碌中）。
    """
    spec = tasks.TASKS[task_id]
    argv = tasks.build_argv(spec, params)
    if not runner.try_mark("task", task_id, spec.label):
        raise TaskBusy()
    try:
        async with get_sessionmaker()() as s:
            row = JobRun(
                task_id=task_id, params=params, status="running", source=source,
                data_version=data_version(get_thresholds().raw),
            )
            s.add(row)
            await s.commit()
            run_id = row.id
    except Exception as e:
        runner.finish(ok=False, error=str(e))
        raise
    _buffers[run_id] = []
    _running[run_id] = asyncio.create_task(_execute(run_id, spec, argv))
    logger.info("工作啟動 #%d %s argv=%s（%s）", run_id, task_id, argv, source)
    return run_id


async def _execute(run_id: int, spec: tasks.TaskSpec, argv: list[str]) -> None:
    buf = _buffers[run_id]
    status, code, error = "failed", None, None
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", spec.module, *argv,
            cwd=str(PROJECT_ROOT),
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            preexec_fn=_lower_priority if os.name == "posix" else None,
        )
        _procs[run_id] = proc
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip()
            buf.append(line)
            runner._state["step"] = f"{spec.label}：{line[:120]}"
        code = await proc.wait()
        if run_id in _cancelled:
            status = "cancelled"
        else:
            status = "succeeded" if code == 0 else "failed"
    except Exception as e:  # noqa: BLE001 — 啟動失敗也要落紀錄並釋放鎖
        logger.exception("工作執行失敗 #%d", run_id)
        buf.append(f"[執行器錯誤] {e}")
        error = str(e)
    finally:
        _procs.pop(run_id, None)
        output = _trim("\n".join(buf))
        try:
            async with get_sessionmaker()() as s:
                await s.execute(
                    update(JobRun).where(JobRun.id == run_id).values(
                        status=status, exit_code=code, output=output,
                        finished_at=dt.datetime.now(_TZ),
                    )
                )
                await s.commit()
        finally:
            runner._state["result"] = {"task": spec.id, "run_id": run_id, "status": status}
            runner.finish(ok=status == "succeeded", error=error or (
                None if status == "succeeded" else f"{spec.label} {status}（exit={code}）"
            ))
            _buffers.pop(run_id, None)
            _cancelled.discard(run_id)
            logger.info("工作結束 #%d %s status=%s exit=%s", run_id, spec.id, status, code)


def live_output(run_id: int) -> str | None:
    """執行中工作的即時輸出；已結束回 None（改讀 DB）。"""
    buf = _buffers.get(run_id)
    return None if buf is None else _trim("\n".join(buf))


def is_running(run_id: int) -> bool:
    return run_id in _running and not _running[run_id].done()


def cancel(run_id: int) -> bool:
    proc = _procs.get(run_id)
    if proc is None or proc.returncode is not None:
        return False
    _cancelled.add(run_id)
    proc.send_signal(signal.SIGTERM)
    grace = float(_cfg().get("cancel_grace_sec", 5))

    async def _kill_later() -> None:
        await asyncio.sleep(grace)
        if proc.returncode is None:
            proc.kill()

    asyncio.get_running_loop().create_task(_kill_later())
    return True


async def wait(run_id: int) -> JobRun:
    task = _running.get(run_id)
    if task is not None:
        await task
        _running.pop(run_id, None)
    async with get_sessionmaker()() as s:
        return (await s.execute(select(JobRun).where(JobRun.id == run_id))).scalar_one()


async def mark_interrupted() -> int:
    """API 啟動時呼叫：上次 process 結束時仍標 running 的紀錄改為 interrupted。"""
    async with get_sessionmaker()() as s:
        res = await s.execute(
            update(JobRun)
            .where(JobRun.status == "running", JobRun.id.not_in(list(_running) or [-1]))
            .values(status="interrupted", finished_at=dt.datetime.now(_TZ))
        )
        await s.commit()
        return res.rowcount or 0
