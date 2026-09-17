"""FastAPI 應用進入點。

Phase 1（Daily Chip Scanner）：提供 /health 與籌碼分析/掃描 API。
啟動：uvicorn app.main:app --reload
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth_deps import require_user

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info("啟動 tw_chip_analyzer（env=%s）", settings.app_env)
    # EOD 排程器隨 API server 起停(test / schedule.enabled=false 時不啟動)
    from app.jobs.scheduler import shutdown_scheduler, start_scheduler

    start_scheduler()
    if not settings.is_test:
        # 上次 process 結束時仍在跑的腳本工作 → 標為 interrupted（子行程已隨之失聯）
        from app.jobs.task_runner import mark_interrupted

        try:
            await mark_interrupted()
        except Exception as e:  # noqa: BLE001 — 表未 migrate 等不應阻擋啟動
            logger.warning("標記中斷工作失敗：%s", e)
    yield
    shutdown_scheduler()
    from app.db.session import reset_engine

    await reset_engine()
    logger.info("關閉 tw_chip_analyzer")


def create_app() -> FastAPI:
    app = FastAPI(
        title="台股籌碼分析與進出場建議系統",
        version="0.1.0",
        description="Phase 1: Daily Chip Scanner",
        lifespan=lifespan,
    )

    # 允許本機前端(Next.js dev)跨埠呼叫。認證改用 cookie 後必須 allow_credentials；
    # 帶 credentials 的請求不接受 "*" 萬用值，故 methods / headers 明列。
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-Ops-Key"],
    )

    @app.get("/health", tags=["system"])
    async def health() -> dict:
        return {"status": "ok", "env": get_settings().app_env}

    from app.api.auth import router as auth_router
    from app.api.dashboard import router as dashboard_router
    from app.api.heatmap import router as heatmap_router
    from app.api.logos import router as logos_router
    from app.api.notify import router as notify_router
    from app.api.ops import router as ops_router
    from app.api.scanner import router as scanner_router
    from app.api.settings import router as settings_router
    from app.api.tasks import router as tasks_router
    from app.api.stocks import router as stocks_router
    from app.api.validation import router as validation_router

    # /api/auth/* 本身不能要求登入(否則沒人進得來);其餘一律至少需要已認證身分。
    # 尚未建立任何帳號時 require_user 走相容模式放行——見 app/api/auth_deps。
    app.include_router(auth_router)
    protected = [Depends(require_user)]
    app.include_router(stocks_router, dependencies=protected)
    app.include_router(scanner_router, dependencies=protected)
    app.include_router(dashboard_router, dependencies=protected)
    app.include_router(heatmap_router, dependencies=protected)
    app.include_router(logos_router, dependencies=protected)
    app.include_router(ops_router, dependencies=protected)
    app.include_router(settings_router, dependencies=protected)
    app.include_router(notify_router, dependencies=protected)
    app.include_router(tasks_router, dependencies=protected)
    app.include_router(validation_router, dependencies=protected)

    return app


app = create_app()
