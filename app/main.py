"""FastAPI 應用進入點。

Phase 1（Daily Chip Scanner）：提供 /health 與籌碼分析/掃描 API。
啟動：uvicorn app.main:app --reload
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info("啟動 tw_chip_analyzer（env=%s）", settings.app_env)
    yield
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

    @app.get("/health", tags=["system"])
    async def health() -> dict:
        return {"status": "ok", "env": get_settings().app_env}

    from app.api.scanner import router as scanner_router
    from app.api.stocks import router as stocks_router

    app.include_router(stocks_router)
    app.include_router(scanner_router)

    return app


app = create_app()
