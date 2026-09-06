"""pytest 共用 fixtures。

測試一律使用測試資料庫（APP_ENV=test → TEST_DATABASE_URL），
每個測試 session 重建 schema，確保隔離。
"""
from __future__ import annotations

import os

# 必須在匯入任何 app.* 之前設定，讓 Settings 讀到 test 環境
os.environ["APP_ENV"] = "test"

import pytest
import pytest_asyncio

from app.core.config import get_settings
from app.db.base import Base
from app.db.session import get_engine, get_sessionmaker, reset_engine


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest_asyncio.fixture(scope="function")
async def db_session():
    settings = get_settings()
    assert settings.is_test, "測試必須跑在 test 環境"

    await reset_engine()
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await reset_engine()
