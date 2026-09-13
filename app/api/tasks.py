"""系統頁「工作」API：列出白名單腳本、觸發、查輸出、取消。

觸發/取消需 ops 管理者認證並寫稽核 log；列表與輸出唯讀免金鑰。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.ops_auth import client_desc, require_ops_auth
from app.core.logging import get_logger
from app.db.models.jobs import JobRun
from app.db.session import get_session
from app.jobs import task_runner, tasks

router = APIRouter(prefix="/api/ops/tasks", tags=["ops"])
audit = get_logger("api.ops.audit")

_RECENT_RUNS = 20


class StartTask(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)


def _run_dict(r: JobRun, with_output: bool) -> dict:
    out = {
        "id": r.id, "task_id": r.task_id, "params": r.params, "status": r.status,
        "exit_code": r.exit_code, "source": r.source, "data_version": r.data_version,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
    }
    if with_output:
        live = task_runner.live_output(r.id)
        out["output"] = live if live is not None else (r.output or "")
    return out


@router.get("")
async def list_tasks(session: AsyncSession = Depends(get_session)) -> dict:
    runs = (await session.execute(
        select(JobRun).order_by(JobRun.id.desc()).limit(_RECENT_RUNS)
    )).scalars().all()
    return {
        "tasks": [tasks.spec_to_dict(t) for t in tasks.TASKS.values()],
        "runs": [_run_dict(r, with_output=False) for r in runs],
    }


@router.post("/runs/{run_id}/cancel")
async def cancel_run(
    run_id: int, request: Request, auth: str = Depends(require_ops_auth)
) -> dict:
    ok = task_runner.cancel(run_id)
    audit.info("ops 稽核 允許 取消工作 #%d ok=%s %s auth=%s", run_id, ok, client_desc(request), auth)
    return {"cancelled": ok}


@router.get("/runs/{run_id}")
async def get_run(run_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    row = (await session.execute(select(JobRun).where(JobRun.id == run_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="找不到此執行紀錄")
    return _run_dict(row, with_output=True)


@router.post("/{task_id}")
async def start_task(
    task_id: str, body: StartTask, request: Request, auth: str = Depends(require_ops_auth)
) -> dict:
    source = f"{client_desc(request)} auth={auth}"
    try:
        run_id = await task_runner.start(task_id, body.params, source=source)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"不支援的工作：{task_id}")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except task_runner.TaskBusy:
        raise HTTPException(status_code=409, detail="已有 EOD / 回補 / 工作進行中，請稍候")
    audit.info("ops 稽核 允許 工作 %s #%d params=%s %s", task_id, run_id, body.params, source)
    return {"run_id": run_id}
